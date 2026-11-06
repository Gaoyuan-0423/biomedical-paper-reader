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
	"reflect"
	"runtime"
	"strings"
	"time"

	"github.com/egoist/mygo"
)

var buildCommit = "development"
var buildDirty = "true"

const syntheticCaptionFirstLine = "Samples were collected from three independent synthetic groups."
const syntheticCaptionSecondLine = "Each group was measured twice using the same imaging settings."

const syntheticFigureObservation = "## Saved figure observation\n\n" +
	"- Synthetic group A was measured twice.\n- Synthetic group B was measured twice.\n\n" +
	"| Group | Repeats |\n| --- | --- |\n| A | 2 |\n| B | 2 |\n\n" +
	"This is a pre-existing synthetic observation used only to check the desktop viewer. " +
	"It is stored with the figure and must remain independent from the paper-level note. " +
	"Opening, closing, and reopening the viewer must show the complete saved content " +
	"without sending a model request, replacing the note, or reducing this paragraph to a card preview.\n\n" +
	"Final saved observation sentence."

// Runs inside the real native WebView after the existing locale/icon checks.
// Click actual gallery entry points so a correctly exported but unwired module
// cannot satisfy the packaged-app acceptance test.
const nativeFigureReaderSmoke = `
window.__bioreaderSmokeStage = 'saved figure observation';
const waitForFigure = async (test, message) => {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const result = test();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(message);
};
const paperAddress = '/api/papers/' + papers[0].id;
const beforeNoteResponse = await fetch(paperAddress);
verify(beforeNoteResponse.ok, 'Could not read the synthetic paper before opening the viewer');
const beforeNote = (await beforeNoteResponse.json()).notes;
const figureResponse = await fetch(paperAddress + '/figures');
verify(figureResponse.ok, 'Could not read saved figures');
const savedFigure = (await figureResponse.json()).find(figure => figure.observation && figure.observation.includes('Final saved observation sentence.'));
verify(savedFigure, 'Saved figure observation fixture is missing');
const libraryEntry = await waitForFigure(() => [...document.querySelectorAll('.paper-open')].find(node => node.dataset.id === papers[0].id), 'Paper library entry was not rendered');
libraryEntry.click();
await waitForFigure(() => [...document.querySelectorAll('.paper-item.active')].some(node => node.dataset.id === papers[0].id), 'Synthetic paper did not open');
document.querySelector('#view-figures').click();
const thumbnail = await waitForFigure(() => [...document.querySelectorAll('#fig-grid [data-zoom]')].find(node => node.dataset.zoom === savedFigure.id), 'Figure thumbnail was not rendered');
thumbnail.click();
const assertFigureViewer = async () => {
  const viewer = await waitForFigure(() => {
    const node = document.querySelector('#figure-reader');
    return node?.dataset.state === 'open' && node.dataset.imageState === 'ready' && node.dataset.notesState === 'ready' ? node : null;
  }, 'Figure viewer did not load the saved image and observation');
  const markdown = document.querySelector('#figure-reader-markdown');
  verify(markdown.querySelector('h2')?.textContent.trim() === 'Saved figure observation', 'Saved observation heading was not rendered as Markdown');
  verify(markdown.querySelectorAll('li').length === 2 && markdown.querySelectorAll('table tbody tr').length === 2, 'Saved observation list or table was not rendered');
  verify(markdown.textContent.includes('Final saved observation sentence.') && markdown.textContent.length > 400, 'Saved observation was truncated');
  const image = document.querySelector('#figure-reader-image');
  verify(image.complete && image.naturalWidth > 0 && image.src.includes(paperAddress + '/figures/' + savedFigure.id + '.png'), 'Figure reader did not load the current paper image');
  const imagePane = document.querySelector('#figure-reader-viewport').getBoundingClientRect();
  const notePane = document.querySelector('#figure-reader-notes').getBoundingClientRect();
  verify(imagePane.width > 0 && imagePane.height > 0 && notePane.width > 0 && notePane.height > 0 && (imagePane.right <= notePane.left + 1 || imagePane.bottom <= notePane.top + 1), 'Figure image and note panes are not separately laid out');
  return viewer;
};
let viewer = await assertFigureViewer();
viewer.querySelector('[data-figure-reader-action="close"]').click();
await waitForFigure(() => !document.querySelector('#figure-reader'), 'Figure reader did not close');
const reopen = [...document.querySelectorAll('#fig-grid [data-view-figure-notes]')].find(node => node.dataset.viewFigureNotes === savedFigure.id);
verify(reopen, 'View notes action is missing');
reopen.click();
viewer = await assertFigureViewer();
viewer.querySelector('[data-figure-reader-action="close"]').click();
await waitForFigure(() => !document.querySelector('#figure-reader'), 'Reopened figure reader did not close');
const afterNoteResponse = await fetch(paperAddress);
verify(afterNoteResponse.ok && (await afterNoteResponse.json()).notes === beforeNote, 'Opening saved figure observations changed paper-level notes');
const afterFiguresResponse = await fetch(paperAddress + '/figures');
verify(afterFiguresResponse.ok && (await afterFiguresResponse.json()).find(figure => figure.id === savedFigure.id)?.observation === savedFigure.observation, 'Opening saved figure observations changed the stored observation');
`

const nativeAppearanceSmoke = `
window.__bioreaderSmokeStage = 'appearance settings';
verify(window.BioAppearance, 'Appearance module is unavailable');
document.querySelector('#view-preview').click();
await window.BioChat.open(papers[0]);
const appearanceDraft = document.querySelector('#chat-input');
const appearanceHistory = document.querySelector('#chat-history');
const appearanceEditor = document.querySelector('#editor');
const appearancePreview = document.querySelector('#preview');
verify(appearanceDraft && appearanceEditor && appearancePreview, 'Appearance content fixtures are missing');
const appearanceQuestion = 'Keep this draft · 外观切换不改内容';
appearanceDraft.value = appearanceQuestion;
appearanceDraft.dispatchEvent(new Event('input', {bubbles: true}));
const originalEditor = appearanceEditor.value;
const originalPreview = appearancePreview.textContent;
const originalLocale = document.documentElement.lang;
const appearancePaperResponse = await fetch(paperAddress);
verify(appearancePaperResponse.ok, 'Could not read notes before appearance changes');
const appearancePaper = await appearancePaperResponse.json();
document.querySelector('#btn-settings').click();
await waitForFigure(() => !document.querySelector('#settings-modal').hidden, 'Settings did not open');
const appearanceControls = {
  scheme: document.querySelector('#appearance-scheme'),
  accent: document.querySelector('#appearance-accent'),
  ui_font_size: document.querySelector('#appearance-ui-font'),
  note_font_size: document.querySelector('#appearance-note-font'),
};
verify(Object.values(appearanceControls).every(Boolean), 'Appearance settings controls are missing');
const fontSize = element => parseFloat(getComputedStyle(element).fontSize);
const closeEnough = (a, b) => Math.abs(a - b) < 0.15;
const uiProbe = document.querySelector('#btn-settings');
const initialUIFont = fontSize(uiProbe);
const initialEditorFont = fontSize(appearanceEditor);
const initialPreviewFont = fontSize(appearancePreview);
const changeAppearance = async (field, value) => {
  const control = appearanceControls[field];
  control.value = String(value);
  control.dispatchEvent(new Event(control.type === 'range' ? 'input' : 'change', {bubbles: true}));
  verify(await window.BioAppearance.flush(), 'Appearance settings were not saved');
  const response = await fetch('/api/ui-preferences');
  verify(response.ok, 'Saved appearance preferences could not be read');
  const preferences = await response.json();
  verify(preferences[field] === value && preferences.locale === originalLocale, 'Appearance persistence changed another preference or lost its value');
  verify(document.documentElement.dataset.appearanceState === 'ready', 'Appearance preferences did not reach the saved state');
  return preferences;
};
await changeAppearance('ui_font_size', 17);
verify(closeEnough(fontSize(document.documentElement), 17) && fontSize(uiProbe) > initialUIFont + 1, 'Interface font size did not change computed styles');
verify(closeEnough(fontSize(appearanceEditor), initialEditorFont) && closeEnough(fontSize(appearancePreview), initialPreviewFont), 'Interface font size changed the independent note size');
const enlargedUIFont = fontSize(uiProbe);
await changeAppearance('note_font_size', 22);
verify(closeEnough(fontSize(appearanceEditor), 22) && closeEnough(fontSize(appearancePreview), 22), 'Note font size did not affect both editor and preview');
verify(closeEnough(fontSize(uiProbe), enlargedUIFont) && closeEnough(fontSize(document.documentElement), 17), 'Note font size changed the interface size');
const accentColors = new Set();
const accentSamples = [];
for (const accent of ['blue', 'teal', 'violet']) {
  await changeAppearance('accent', accent);
  verify(document.documentElement.dataset.accent === accent, 'Accent selection did not reach the document');
  // Visible engines complete this 150 ms transition naturally. A hidden
  // WKWebView freezes its animation timeline at zero despite elapsed timers,
  // so finish only this probe's finite CSS transitions before sampling the
  // actual computed color. The product styles and three-color check stay intact.
  await new Promise(resolve => setTimeout(resolve, 220));
  const accentButton = document.querySelector('#settings-save');
  if (document.visibilityState === 'hidden') {
    for (const animation of accentButton.getAnimations()) {
      if (animation instanceof CSSTransition && Number.isFinite(animation.effect.getComputedTiming().endTime)) animation.finish();
    }
  }
  const renderedAccent = getComputedStyle(accentButton).backgroundColor;
  accentColors.add(renderedAccent);
  accentSamples.push({accent, renderedAccent, target: getComputedStyle(accentButton).getPropertyValue('--primary')});
}
verify(accentColors.size === 3, 'Accent presets do not produce three distinct control colors: ' + JSON.stringify(accentSamples));
await changeAppearance('scheme', 'dark');
verify(document.documentElement.dataset.colorScheme === 'dark', 'Dark appearance did not apply');
const darkBackground = getComputedStyle(document.body).backgroundColor;
await changeAppearance('scheme', 'light');
verify(document.documentElement.dataset.colorScheme === 'light' && getComputedStyle(document.body).backgroundColor !== darkBackground, 'Light and dark appearances have the same rendered background');
await changeAppearance('scheme', 'system');
verify(document.documentElement.dataset.colorScheme === (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'), 'System appearance does not match the native WebView media query');
const themeMeta = document.querySelector('meta[name="theme-color"]');
verify(themeMeta && themeMeta.content, 'Native window theme-color metadata is missing');
verify(document.documentElement.lang === originalLocale && controls.every(control => control.value === originalLocale), 'Appearance changes modified the interface language');
verify(document.querySelector('#chat-input') === appearanceDraft && appearanceDraft.value === appearanceQuestion && document.querySelector('#chat-history') === appearanceHistory, 'Appearance changes replaced the chat draft or history');
verify(document.querySelector('#editor') === appearanceEditor && appearanceEditor.value === originalEditor && appearancePreview.textContent === originalPreview, 'Appearance changes replaced or modified paper notes');
const finalAppearanceResponse = await fetch('/api/ui-preferences');
verify(finalAppearanceResponse.ok, 'Final appearance preferences could not be read');
const finalAppearance = await finalAppearanceResponse.json();
verify(finalAppearance.scheme === 'system' && finalAppearance.accent === 'violet' && finalAppearance.ui_font_size === 17 && finalAppearance.note_font_size === 22 && finalAppearance.locale === originalLocale, 'Appearance preference fields were not independently persisted');
const unchangedPaperResponse = await fetch(paperAddress);
verify(unchangedPaperResponse.ok && (await unchangedPaperResponse.json()).notes === appearancePaper.notes, 'Appearance settings changed stored paper notes');
document.querySelector('#settings-close').click();
await waitForFigure(() => document.querySelector('#settings-modal').hidden, 'Settings did not close after the appearance check');
`

func syntheticPDF() []byte {
	// Keep the searchable text and PDF.js smoke marker, while exercising a
	// standalone supplement title followed by smaller caption body lines.
	stream := strings.Join([]string{
		"BT /F1 12 Tf 60 740 Td (BioReader synthetic desktop diagnostic.) Tj ET",
		"q 0.85 0.9 1 rg 60 440 400 240 re f Q",
		"BT /F1 13 Tf 60 420 Td (Supplementary Figure 1) Tj ET",
		"BT /F1 9.5 Tf 60 402 Td (" + syntheticCaptionFirstLine + ") Tj ET",
		"BT /F1 9.5 Tf 60 388 Td (" + syntheticCaptionSecondLine + ") Tj ET",
	}, "\n")
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

func exerciseCaptionUpgrade(service *serviceProcess, client *http.Client, paperID, figureID string, initial []byte, report map[string]any) error {
	address := service.ready.URL + "/api/papers/" + paperID
	directory := filepath.Join(service.dataDirectory, "papers", paperID)
	primary := filepath.Join(directory, "figures.json")
	var legacy []map[string]any
	if err := json.Unmarshal(initial, &legacy); err != nil {
		return err
	}
	var expected map[string]any
	for _, figure := range legacy {
		if figure["id"] == figureID {
			figure["caption"] = "Supplementary Figure 1"
			figure["observation"] = syntheticFigureObservation
			delete(figure, "caption_edited")
			expected = figure
		}
	}
	if expected == nil {
		return errors.New("legacy caption diagnostic figure is missing")
	}
	png, err := requestBytes(client, "GET", address+"/figures/"+figureID+".png", "", nil, 200)
	if err != nil {
		return err
	}
	legacyJSON, err := json.Marshal(legacy)
	if err != nil {
		return err
	}
	// This directory belongs to runDiagnostic's disposable synthetic library.
	// Simulate records saved by an old extractor without using a caption PATCH,
	// which would correctly mark the text as a deliberate manual edit.
	if err = os.WriteFile(primary, legacyJSON, 0600); err != nil {
		return err
	}
	if err = os.Remove(filepath.Join(directory, "caption-migrations.json")); err != nil && !os.IsNotExist(err) {
		return err
	}
	check := func(data []byte) error {
		var fresh []map[string]any
		if err := json.Unmarshal(data, &fresh); err != nil {
			return err
		}
		if len(fresh) != len(legacy) {
			return errors.New("caption upgrade changed the figure count")
		}
		for _, figure := range fresh {
			if figure["id"] != figureID {
				continue
			}
			caption, _ := figure["caption"].(string)
			if !strings.Contains(caption, syntheticCaptionFirstLine) || !strings.Contains(caption, syntheticCaptionSecondLine) {
				return errors.New("opening legacy figures did not automatically restore both caption body lines")
			}
			figure["caption"] = expected["caption"]
			if !reflect.DeepEqual(figure, expected) {
				return errors.New("caption upgrade changed figure identity, crop, or saved observation")
			}
			return nil
		}
		return errors.New("caption upgrade removed the original figure ID")
	}
	upgraded, err := requestBytes(client, "GET", address+"/figures", "", nil, 200)
	if err != nil {
		return err
	}
	if err = check(upgraded); err != nil {
		return err
	}
	afterPNG, err := requestBytes(client, "GET", address+"/figures/"+figureID+".png", "", nil, 200)
	if err != nil {
		return err
	}
	if !bytes.Equal(png, afterPNG) {
		return errors.New("automatic caption upgrade regenerated the existing crop")
	}
	report["caption_upgrade_tested"] = true
	// A further explicit save preserves this generation as the recovery backup.
	observation, err := json.Marshal(map[string]string{"observation": syntheticFigureObservation})
	if err != nil {
		return err
	}
	if _, err = requestBytes(client, "PATCH", address+"/figures/"+figureID, "application/json", observation, 200); err != nil {
		return err
	}
	backupPath := filepath.Join(directory, "figures.json.bak")
	backup, err := os.ReadFile(backupPath)
	if err != nil {
		return err
	}
	damaged := []byte(`[{"id":`)
	if err = os.WriteFile(primary, damaged, 0600); err != nil {
		return err
	}
	recovered, err := requestBytes(client, "GET", address+"/figures", "", nil, 200)
	if err != nil {
		return err
	}
	if err = check(recovered); err != nil {
		return fmt.Errorf("figure backup recovery: %w", err)
	}
	retained, err := os.ReadFile(primary)
	if err != nil || !bytes.Equal(retained, damaged) {
		return errors.New("reading a figure backup overwrote the damaged original")
	}
	retained, err = os.ReadFile(backupPath)
	if err != nil || !bytes.Equal(retained, backup) {
		return errors.New("reading a figure backup changed the backup")
	}
	report["figure_backup_tested"] = true
	// Restore the synthetic primary through an explicit API save for the later
	// native viewer diagnostic; reading alone above must never perform this write.
	_, err = requestBytes(client, "PATCH", address+"/figures/"+figureID, "application/json", observation, 200)
	return err
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
	data, err = requestBytes(client, "GET", address+"/figures", "", nil, 200)
	if err != nil {
		return err
	}
	var figures []struct {
		ID      string `json:"id"`
		Page    int    `json:"page"`
		Caption string `json:"caption"`
	}
	if err = json.Unmarshal(data, &figures); err != nil {
		return err
	}
	completeCaption := false
	figureID := ""
	for _, figure := range figures {
		if figure.Page == 1 && strings.Contains(figure.Caption, "Supplementary Figure 1") &&
			strings.Contains(figure.Caption, syntheticCaptionFirstLine) && strings.Contains(figure.Caption, syntheticCaptionSecondLine) {
			completeCaption = true
			figureID = figure.ID
			break
		}
	}
	if !completeCaption {
		return errors.New("supplementary figure caption lost its smaller-font body lines")
	}
	report["caption_tested"] = true
	if err = exerciseCaptionUpgrade(service, client, id, figureID, data, report); err != nil {
		return err
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
		report["figure_reader_tested"] = true
		report["appearance_tested"] = true
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
	report["checks"] = []string{"Real bundled sidecar on a random loopback socket", "Private owner handshake, required session, foreign-origin rejection", "Bundled frontend and native PDF import/retrieval/search", "Frozen backend extracts both smaller-font supplementary caption lines below a standalone figure title", "Opening legacy title-only captions automatically restores both body lines without changing IDs, crops or saved observations", "Damaged figure records recover saved observations from a valid backup while leaving both original files unchanged", "Unicode note persistence and deletion", "Child service exits when its owner pipe closes"}
	if gui {
		report["checks"] = append(report["checks"].([]string), "Native MyGo WebView loads the frontend, authenticated fetch, PDF.js worker and canvas render", "Chinese/English interface switching preserves the chat composer and Unicode draft, synchronizes both controls and persists the native dialog language", "Brand SVG loads and decodes in the native WebView", "Gallery thumbnail and View notes open the saved figure observation as complete Markdown beside its local image; close/reopen preserves both observation and paper note", "Native settings controls independently resize interface and notes, persist three accent presets and system/light/dark appearance, preserve language and research drafts, and update the native window background")
	}
	return nil
}

func runDiagnostic(executable, reportPath string, gui bool) (resultErr error) {
	report := map[string]any{"status": "failed", "platform": runtime.GOOS, "architecture": runtime.GOARCH, "desktop": "egoist/mygo", "mygo_version": "v0.7.4", "gui_tested": false, "locale_tested": false, "brand_icon_tested": false, "caption_tested": false, "caption_upgrade_tested": false, "figure_backup_tested": false, "figure_reader_tested": false, "appearance_tested": false,
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
