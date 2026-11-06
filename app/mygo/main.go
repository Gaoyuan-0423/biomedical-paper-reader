package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"image/color"
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

func configureWindow(win *mygo.Window, origin, dataDirectory string) *atomic.Bool {
	text := func(chinese, english string) string { return nativeText(dataDirectory, chinese, english) }
	page := win.Page()
	var backgroundUpdated atomic.Bool
	// Keep the native app appearance on the system setting so matchMedia stays
	// truthful. The page's theme-color updates only the surface behind its CSS.
	page.OnThemeColorChanged(func(c color.NRGBA, ok bool) {
		if ok && sameOrigin(page.URL(), origin) {
			if err := win.SetBackgroundColor(fmt.Sprintf("#%02x%02x%02x", c.R, c.G, c.B)); err == nil {
				backgroundUpdated.Store(true)
			}
		}
	})
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
				_, _ = mygo.Dialog.Message(mygo.MessageOptions{Parent: win, Type: mygo.MessageWarning, Title: text("暂时无法关闭", "Cannot close yet"), Message: text("尚未确认笔记已经保存，请回到阅读界面保存后重试。", "Your notes may not be saved. Return to the reading window, save them and try again.")})
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
		path, err := mygo.Dialog.Save(mygo.SaveDialogOptions{Parent: win, Title: text("保存导出文件", "Save exported file"), DefaultPath: name})
		if err != nil || path == "" {
			event.PreventDefault()
			return
		}
		event.Path = path
	})
	page.OnDownloadDone(func(download *mygo.Download) {
		if download.Err != nil {
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{Parent: win, Type: mygo.MessageError, Title: text("导出未完成", "Export did not finish"), Message: download.Err.Error()})
		}
	})
	return &backgroundUpdated
}

func completedWindowResult(result <-chan error, smoke bool) error {
	select {
	case err := <-result:
		return err
	default:
		if smoke {
			return errors.New("native WebView closed before its diagnostic completed")
		}
		return nil
	}
}

func runWindow(service *serviceProcess, smoke bool) error {
	text := func(chinese, english string) string { return nativeText(service.dataDirectory, chinese, english) }
	// Completion is explicit and synchronized: an early normal App.Run return
	// must not turn an unfinished native diagnostic into a successful result.
	result := make(chan error, 1)
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
	finish := func(err error) { once.Do(func() { result <- err; mygo.App.Quit() }) }
	mygo.App.WhenReady(func() {
		defer func() {
			if failure := recover(); failure != nil {
				err := fmt.Errorf("%s: %v", text("阅读窗口无法启动", "The reading window could not start"), failure)
				if !smoke {
					detail := err.Error()
					if runtime.GOOS == "windows" {
						detail += "\n\n" + text("请安装 Microsoft Edge WebView2 Runtime：", "Install the Microsoft Edge WebView2 Runtime:") + "\nhttps://developer.microsoft.com/en-us/microsoft-edge/webview2/"
					}
					_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: text("BioReader 无法启动", "BioReader could not start"), Message: text("无法创建阅读界面。", "The reading interface could not be created."), Detail: detail})
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
			MinWidth: 800, MinHeight: 600, Hidden: smoke, StateKey: stateKey, BackgroundColor: "light-dark(#f5f8ff, #0f172a)",
			Page: mygo.PageOptions{TrustedOrigins: []string{service.ready.URL}, Session: session, DevTools: mygo.DevToolsDisabled}})
		mainWindow = win
		backgroundUpdated := configureWindow(win, service.ready.URL, service.dataDirectory)
		if smoke {
			win.Page().OnDidFinishLoad(func() {
				if !sameOrigin(win.Page().URL(), service.ready.URL) {
					return
				}
				// Older WKWebView versions defer variable-driven layout while hidden.
				// Validate the same visible, synthetic interface a user would see.
				win.Show()
				go func() {
					ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
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
window.__bioreaderSmokeStage = 'interface locale';
const verify = (condition, message) => { if (!condition) throw new Error(message); };
verify(window.BioI18n && window.BioChat, 'Reading interface modules are unavailable');
const controls = [...document.querySelectorAll('[data-ui-locale]')];
verify(controls.length === 2, 'Both interface language controls must be present');
await window.BioChat.open(papers[0]);
const draft = document.querySelector('#chat-input'), history = document.querySelector('#chat-history');
verify(draft && history, 'Chat composer did not initialize');
const originalDraft = '中文草稿 · Keep this question';
draft.value = originalDraft;
draft.dispatchEvent(new Event('input', {bubbles: true}));
draft.setSelectionRange(2, 5);
for (const locale of ['en', 'zh-CN', 'en']) {
  verify(await window.BioI18n.setLocale(locale), 'Interface language was not saved');
  verify(document.documentElement.lang === locale && controls.every(control => control.value === locale), 'Language controls are not synchronized');
  verify(document.querySelector('#chat-input') === draft && draft.value === originalDraft && draft.selectionStart === 2 && draft.selectionEnd === 5, 'Switching languages changed the chat draft or composer');
  verify(document.querySelector('#chat-history') === history, 'Switching languages replaced chat history');
  const settings = document.querySelector('#btn-settings [data-i18n="设置"]');
  verify(settings && settings.textContent.trim() === (locale === 'en' ? 'Settings' : '设置'), 'Native WebView did not translate the visible interface');
}
const savedLocaleResponse = await fetch('/api/ui-preferences');
verify(savedLocaleResponse.ok && (await savedLocaleResponse.json()).locale === 'en', 'Interface language was not persisted by the bundled backend');
window.__bioreaderSmokeStage = 'brand icon';
const icon = document.querySelector('.brand-mark img');
verify(icon && icon.getAttribute('src') === 'brand-mark.svg', 'Brand icon is missing');
if (icon.decode) await icon.decode();
verify(icon.complete && icon.naturalWidth > 0 && icon.naturalHeight > 0, 'Brand icon did not load in the native WebView');
`+nativeFigureReaderSmoke+`
`+nativeAppearanceSmoke+`
window.__bioreaderSmokeStage = 'complete';
return text.items.some(item => item.str.includes('BioReader synthetic'));
})()`)
					fontProbe, stopFontProbe := context.WithTimeout(context.Background(), 2*time.Second)
					fontDetails, _ := win.Page().EvalContext(fontProbe, "JSON.stringify(window.__bioreaderAppearanceFonts || null)")
					stopFontProbe()
					if details, ok := fontDetails.(string); ok && details != "null" {
						fmt.Println("Native appearance font measurements:", details)
					}
					if err != nil {
						probe, stop := context.WithTimeout(context.Background(), 2*time.Second)
						stage, _ := win.Page().EvalContext(probe, "window.__bioreaderSmokeStage || document.readyState")
						stop()
						err = fmt.Errorf("native WebView smoke at %v: %w", stage, err)
					}
					if err == nil && value != true {
						err = fmt.Errorf("WebView frontend check returned %v", value)
					}
					if err == nil && !backgroundUpdated.Load() {
						err = errors.New("native window did not apply the page theme color")
					}
					finish(err)
				}()
			})
			go func() { time.Sleep(45 * time.Second); finish(errors.New("WebView frontend smoke test timed out")) }()
		}
		if err := win.Page().LoadURL(service.bootstrapURL()); err != nil {
			if smoke {
				finish(err)
			} else {
				_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: "BioReader", Message: text("阅读界面加载失败。", "The reading interface could not load."), Detail: err.Error()})
				mygo.App.Quit()
			}
		}
	})
	if err := mygo.App.Run(); err != nil {
		return err
	}
	return completedWindowResult(result, smoke)
}

func main() {
	selfTest := flag.String("self-test", "", "write an isolated packaged-service diagnostic JSON report")
	guiTest := flag.Bool("gui-test", false, "also show a temporary synthetic native MyGo WebView during --self-test")
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
			_, _ = mygo.Dialog.Message(mygo.MessageOptions{Type: mygo.MessageError, Title: nativeText(nativeDataDirectory(), "BioReader 无法启动", "BioReader could not start"), Message: nativeText(nativeDataDirectory(), "阅读服务无法启动，请保留完整应用文件夹后重试。", "The reading service could not start. Keep the complete application folder and try again."), Detail: err.Error()})
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
