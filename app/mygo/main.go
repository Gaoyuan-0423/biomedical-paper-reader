package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/egoist/mygo"
)

func externalURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.User == nil && u.Host != "" && (u.Scheme == "https" || u.Scheme == "http")
}

func sameOrigin(raw, origin string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.User == nil && u.Scheme+"://"+u.Host == origin
}

func configureWindow(win *mygo.Window, origin string) {
	page := win.Page()
	var closeAllowed, checkingClose atomic.Bool
	win.OnClose(func(event *mygo.CloseEvent) {
		if closeAllowed.Load() || !sameOrigin(page.URL(), origin) {
			return
		}
		event.PreventDefault()
		if !checkingClose.CompareAndSwap(false, true) {
			return
		}
		go func() {
			defer checkingClose.Store(false)
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			value, err := page.EvalContext(ctx, `(async () => window.BioDesktop?.prepareClose ? await window.BioDesktop.prepareClose() : true)()`)
			if err == nil && value == true {
				closeAllowed.Store(true)
				win.Close()
			} else if err != nil {
				_, _ = mygo.Dialog.Message(mygo.MessageOptions{Parent: win, Type: mygo.MessageWarning, Title: "暂时无法关闭", Message: "尚未确认笔记已经保存，请回到阅读界面保存后重试。"})
			}
		}()
	})
	page.OnWillNavigate(func(event *mygo.NavigateEvent) {
		if sameOrigin(event.URL, origin) || strings.HasPrefix(event.URL, "blob:"+origin+"/") {
			return
		}
		event.PreventDefault()
		if event.UserInitiated && externalURL(event.URL) {
			go mygo.Shell.OpenExternal(event.URL)
		}
	})
	page.SetWindowOpenHandler(func(request mygo.WindowOpenRequest) *mygo.WindowOptions {
		if request.UserInitiated && externalURL(request.URL) && !sameOrigin(request.URL, origin) {
			go mygo.Shell.OpenExternal(request.URL)
		}
		return nil
	})
	page.OnWillDownload(func(event *mygo.DownloadEvent) {
		if !sameOrigin(event.URL, origin) && !strings.HasPrefix(event.URL, "blob:"+origin+"/") {
			event.PreventDefault()
			return
		}
		name := filepath.Base(strings.ReplaceAll(event.SuggestedName, `\`, "/"))
		if name == "." || name == "" {
			name = "BioReader-export"
		}
		path, err := mygo.Dialog.Save(mygo.SaveDialogOptions{Parent: win, Title: "保存导出文件", DefaultPath: name})
		if err != nil || path == "" {
			event.PreventDefault()
			return
		}
		event.Path = path
	})
	page.OnDownloadDone(func(download *mygo.Download) {
		if download.Err != nil {
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{Parent: win, Type: mygo.MessageError, Title: "导出未完成", Message: download.Err.Error()})
		}
	})
}

func runWindow(service *serviceProcess, smoke bool) error {
	var smokeErr error
	var once sync.Once
	var mainWindow *mygo.Window
	if !smoke {
		mygo.App.OnSecondInstance(func(_ []string, _ string) {
			if mainWindow != nil {
				mainWindow.Restore()
				mainWindow.Show()
				mainWindow.Focus()
			}
		})
	}
	finish := func(err error) { once.Do(func() { smokeErr = err; mygo.App.Quit() }) }
	mygo.App.WhenReady(func() {
		defer func() {
			if failure := recover(); failure != nil {
				err := fmt.Errorf("阅读窗口无法启动: %v", failure)
				if !smoke {
					detail := err.Error()
					if runtime.GOOS == "windows" {
						detail += "\n\n请安装 Microsoft Edge WebView2 Runtime：\nhttps://developer.microsoft.com/en-us/microsoft-edge/webview2/"
					}
					_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: "BioReader 无法启动", Message: "无法创建阅读界面。", Detail: detail})
				}
				finish(err)
			}
		}()
		stateKey := "reader"
		var session *mygo.Session
		if smoke {
			stateKey = ""
			session = mygo.NewEphemeralSession()
		}
		win := mygo.NewWindow(mygo.WindowOptions{Title: "BioReader", URL: "about:blank", Width: 1440, Height: 900,
			MinWidth: 800, MinHeight: 600, Hidden: smoke, StateKey: stateKey, BackgroundColor: "#f5f8ff",
			Page: mygo.PageOptions{TrustedOrigins: []string{service.ready.URL}, Session: session, DevTools: mygo.DevToolsDisabled}})
		mainWindow = win
		configureWindow(win, service.ready.URL)
		if smoke {
			win.Page().OnDidFinishLoad(func() {
				if !sameOrigin(win.Page().URL(), service.ready.URL) {
					return
				}
				go func() {
					ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
					defer cancel()
					value, err := win.Page().EvalContext(ctx, `(async () => {
const response = await fetch('/api/papers');
window.__bioreaderSmokeStage = 'library';
const papers = await response.json();
if (!response.ok || !papers.length || !document.title.includes('BioReader') || !document.querySelector('#toast')) return false;
const pdfResponse = await fetch('/api/papers/' + papers[0].id + '/file');
window.__bioreaderSmokeStage = 'PDF load';
pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdf.worker.min.js';
const pdf = await pdfjsLib.getDocument({data: new Uint8Array(await pdfResponse.arrayBuffer())}).promise;
const page = await pdf.getPage(1), viewport = page.getViewport({scale: 0.2});
const canvas = document.createElement('canvas'); canvas.width = viewport.width; canvas.height = viewport.height;
window.__bioreaderSmokeStage = 'PDF canvas';
await page.render({canvasContext: canvas.getContext('2d'), viewport, intent: 'print'}).promise;
const text = await page.getTextContent();
await pdf.destroy();
window.__bioreaderSmokeStage = 'complete';
return text.items.some(item => item.str.includes('BioReader synthetic'));
})()`)
					if err != nil {
						probe, stop := context.WithTimeout(context.Background(), 2*time.Second)
						stage, _ := win.Page().EvalContext(probe, "window.__bioreaderSmokeStage || document.readyState")
						stop()
						err = fmt.Errorf("native WebView smoke at %v: %w", stage, err)
					}
					if err == nil && value != true {
						err = fmt.Errorf("WebView frontend check returned %v", value)
					}
					finish(err)
				}()
			})
			go func() { time.Sleep(35 * time.Second); finish(errors.New("WebView frontend smoke test timed out")) }()
		}
		if err := win.Page().LoadURL(service.bootstrapURL()); err != nil {
			if smoke {
				finish(err)
			} else {
				_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: "BioReader", Message: "阅读界面加载失败。", Detail: err.Error()})
				mygo.App.Quit()
			}
		}
	})
	if err := mygo.App.Run(); err != nil {
		return err
	}
	return smokeErr
}

func main() {
	selfTest := flag.String("self-test", "", "write an isolated packaged-service diagnostic JSON report")
	guiTest := flag.Bool("gui-test", false, "also open a hidden native MyGo WebView during --self-test")
	serviceOverride := flag.String("service", "", "backend executable override for development")
	flag.Parse()
	mygo.App.SetName("BioReader")
	resources, err := mygo.App.Path(mygo.PathResources)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	executable := *serviceOverride
	if executable == "" {
		executable = filepath.Join(resources, "BioReaderService", "BioReaderService")
		if runtime.GOOS == "windows" {
			executable += ".exe"
		}
	}
	if *selfTest != "" {
		if err := runDiagnostic(executable, *selfTest, *guiTest); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if !mygo.App.RequestSingleInstanceLock() {
		return
	}
	service, err := startService(executable, "")
	if err != nil {
		mygo.App.WhenReady(func() {
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: "BioReader 无法启动", Message: err.Error()})
			mygo.App.Quit()
		})
		_ = mygo.App.Run()
		return
	}
	defer service.stop()
	if err := runWindow(service, false); err != nil {
		service.stop()
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
