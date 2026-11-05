package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/cookiejar"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/egoist/mygo"
)

var buildCommit = "development"
var buildDirty = "true"

func syntheticPDF() []byte {
	stream := "BT /F1 12 Tf 60 740 Td (BioReader synthetic desktop diagnostic.) Tj ET"
	objects := []string{
		"<< /Type /Catalog /Pages 2 0 R >>",
		"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
		fmt.Sprintf("<< /Length %d >>\nstream\n%s\nendstream", len(stream), stream),
		"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	}
	var result bytes.Buffer
	result.WriteString("%PDF-1.4\n")
	offsets := make([]int, len(objects))
	for index, object := range objects {
		offsets[index] = result.Len()
		fmt.Fprintf(&result, "%d 0 obj\n%s\nendobj\n", index+1, object)
	}
	xref := result.Len()
	fmt.Fprintf(&result, "xref\n0 %d\n0000000000 65535 f \n", len(objects)+1)
	for _, offset := range offsets {
		fmt.Fprintf(&result, "%010d 00000 n \n", offset)
	}
	fmt.Fprintf(&result, "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", len(objects)+1, xref)
	return result.Bytes()
}

func requestBytes(client *http.Client, method, address, contentType string, body []byte, expected int) ([]byte, error) {
	request, err := http.NewRequest(method, address, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	if contentType != "" {
		request.Header.Set("Content-Type", contentType)
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode != expected {
		return nil, fmt.Errorf("%s request returned %d, expected %d", method, response.StatusCode, expected)
	}
	return data, nil
}

func exerciseService(service *serviceProcess, report map[string]any, gui bool) error {
	if !service.ready.Frozen {
		return errors.New("diagnostic requires the bundled Python executable")
	}
	client := &http.Client{Timeout: 20 * time.Second}
	if _, err := requestBytes(client, "GET", service.ready.URL+"/api/papers", "", nil, 403); err != nil {
		return err
	}
	if _, err := requestBytes(client, "GET", service.ready.URL+"/", "", nil, 403); err != nil {
		return err
	}
	client.Jar, _ = cookiejar.New(nil)
	if _, err := requestBytes(client, "GET", service.bootstrapURL(), "", nil, 200); err != nil {
		return err
	}
	for _, asset := range []string{"app.js", "styles.css", "vendor/pdf.min.js", "vendor/pdf.worker.min.js", "vendor/docx.umd.js"} {
		data, err := requestBytes(client, "GET", service.ready.URL+"/"+asset, "", nil, 200)
		if err != nil {
			return err
		}
		if len(data) == 0 {
			return fmt.Errorf("empty frontend asset %s", asset)
		}
	}
	request, _ := http.NewRequest("GET", service.ready.URL+"/api/papers", nil)
	request.Header.Set("Origin", "https://example.invalid")
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	response.Body.Close()
	if response.StatusCode != 403 {
		return errors.New("foreign origin was not rejected")
	}
	var upload bytes.Buffer
	form := multipart.NewWriter(&upload)
	file, _ := form.CreateFormFile("file", "diagnostic.pdf")
	_, _ = file.Write(syntheticPDF())
	_ = form.Close()
	data, err := requestBytes(client, "POST", service.ready.URL+"/api/papers", form.FormDataContentType(), upload.Bytes(), 200)
	if err != nil {
		return err
	}
	var paper map[string]any
	if err = json.Unmarshal(data, &paper); err != nil {
		return err
	}
	id, ok := paper["id"].(string)
	if !ok || id == "" {
		return errors.New("PDF import returned no paper ID")
	}
	address := service.ready.URL + "/api/papers/" + id
	data, err = requestBytes(client, "GET", address+"/file", "", nil, 200)
	if err != nil {
		return err
	}
	if !bytes.Equal(data, syntheticPDF()) {
		return errors.New("PDF bytes changed")
	}
	if _, err = requestBytes(client, "PATCH", address, "application/json", []byte(`{"notes":"# 合成诊断\n\nSynthetic note."}`), 200); err != nil {
		return err
	}
	data, err = requestBytes(client, "GET", address, "", nil, 200)
	if err != nil {
		return err
	}
	if !bytes.Contains(data, []byte("Synthetic note.")) {
		return errors.New("note did not persist")
	}
	data, err = requestBytes(client, "GET", service.ready.URL+"/api/library/search?q=synthetic&scope=pdf", "", nil, 200)
	if err != nil {
		return err
	}
	if !bytes.Contains(data, []byte(id)) {
		return errors.New("native PDF parsing/search failed")
	}
	if gui {
		if err = runWindow(service, true); err != nil {
			return err
		}
		if readNativeLocale(service.dataDirectory) != "en" {
			return errors.New("native dialogs did not read the saved interface language")
		}
		report["gui_tested"] = true
		report["locale_tested"] = true
		report["brand_icon_tested"] = true
	}
	if _, err = requestBytes(client, "DELETE", address, "", nil, 200); err != nil {
		return err
	}
	data, err = requestBytes(client, "GET", service.ready.URL+"/api/papers", "", nil, 200)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(data)) != "[]" {
		return errors.New("diagnostic library not empty after deletion")
	}
	report["checks"] = []string{"Real bundled sidecar on a random loopback socket", "Private owner handshake, required session, foreign-origin rejection", "Bundled frontend and native PDF import/retrieval/search", "Unicode note persistence and deletion", "Child service exits when its owner pipe closes"}
	if gui {
		report["checks"] = append(report["checks"].([]string), "Native MyGo WebView loads the frontend, authenticated fetch, PDF.js worker and canvas render", "Chinese/English interface switching preserves the chat composer and Unicode draft, synchronizes both controls and persists the native dialog language", "Brand SVG loads and decodes in the native WebView")
	}
	return nil
}

func runDiagnostic(executable, reportPath string, gui bool) (resultErr error) {
	report := map[string]any{"status": "failed", "platform": runtime.GOOS, "architecture": runtime.GOARCH, "desktop": "egoist/mygo", "mygo_version": "v0.7.4", "gui_tested": false, "locale_tested": false, "brand_icon_tested": false,
		"source_commit": buildCommit, "build_dirty": buildDirty != "false", "app_version": mygo.App.Version()}
	defer func() {
		if resultErr == nil {
			report["status"] = "passed"
		} else {
			report["error"] = resultErr.Error()
		}
		payload, err := json.MarshalIndent(report, "", "  ")
		if err == nil {
			err = os.MkdirAll(filepath.Dir(reportPath), 0755)
		}
		if err == nil {
			err = os.WriteFile(reportPath, append(payload, '\n'), 0600)
		}
		if resultErr == nil {
			resultErr = err
		}
	}()
	directory, err := os.MkdirTemp("", "bioreader-mygo-check-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(directory)
	// MyGo's Windows WebView2 environment reads LOCALAPPDATA directly; the
	// ephemeral page session alone does not isolate its browser profile root.
	if runtime.GOOS == "windows" {
		for _, key := range []string{"LOCALAPPDATA", "APPDATA"} {
			old, existed := os.LookupEnv(key)
			defer func(key, old string, existed bool) {
				if existed {
					_ = os.Setenv(key, old)
				} else {
					_ = os.Unsetenv(key)
				}
			}(key, old, existed)
			path := filepath.Join(directory, key)
			if err = os.Mkdir(path, 0700); err != nil {
				return err
			}
			if err = os.Setenv(key, path); err != nil {
				return err
			}
		}
	}
	for _, item := range []struct {
		name   mygo.PathName
		folder string
	}{
		{mygo.PathUserData, "desktop"}, {mygo.PathCache, "cache"}, {mygo.PathLogs, "logs"},
	} {
		path := filepath.Join(directory, item.folder)
		if err = os.MkdirAll(path, 0700); err != nil {
			return err
		}
		mygo.App.SetPath(item.name, path)
	}
	library := filepath.Join(directory, "library")
	if err = os.Mkdir(library, 0700); err != nil {
		return err
	}
	service, err := startService(executable, library)
	if err != nil {
		return err
	}
	defer service.stop()
	report["backend_frozen"] = service.ready.Frozen
	if err = exerciseService(service, report, gui); err != nil {
		return err
	}
	service.stop()
	client := &http.Client{Timeout: time.Second}
	if response, err := client.Get(service.ready.URL + "/"); err == nil {
		response.Body.Close()
		return errors.New("service remained reachable after owner exit")
	}
	return nil
}
