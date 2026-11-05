package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeLocaleDataDirectoriesMatchBackend(t *testing.T) {
	home := filepath.Join(t.TempDir(), "Profile")
	for _, item := range []struct {
		name, platform, want string
		env                  map[string]string
	}{
		{"macOS", "darwin", filepath.Join(home, "Library", "Application Support", "BioReader"), nil},
		{"Windows fallback", "windows", filepath.Join(home, "AppData", "Local", "BioReader"), nil},
		{"Windows local app data", "windows", filepath.Join(home, "local", "BioReader"), map[string]string{"LOCALAPPDATA": filepath.Join(home, "local")}},
		{"Linux fallback", "linux", filepath.Join(home, ".local", "share", "BioReader"), nil},
		{"Linux XDG", "linux", filepath.Join(home, "xdg", "BioReader"), map[string]string{"XDG_DATA_HOME": filepath.Join(home, "xdg")}},
		{"explicit directory", "darwin", filepath.Join(home, "Custom data"), map[string]string{"BIOREADER_DATA_DIR": filepath.Join(home, "Custom data")}},
		{"tilde home", "darwin", home, map[string]string{"BIOREADER_DATA_DIR": "~"}},
		{"tilde child", "darwin", filepath.Join(home, "Custom data"), map[string]string{"BIOREADER_DATA_DIR": "~/Custom data"}},
		{"Windows tilde child", "windows", filepath.Join(home, "Custom data"), map[string]string{"BIOREADER_DATA_DIR": `~\Custom data`}},
		{"Windows named home", "windows", filepath.Join(home, "custom"), map[string]string{"BIOREADER_DATA_DIR": "~Profile/custom", "USERNAME": "Profile"}},
		{"relative explicit path", "darwin", "relative-library", map[string]string{"BIOREADER_DATA_DIR": "relative-library"}},
	} {
		t.Run(item.name, func(t *testing.T) {
			got := desktopDataDirectory(item.platform, home, func(key string) string { return item.env[key] })
			if got != item.want {
				t.Fatalf("directory = %q, want %q", got, item.want)
			}
		})
	}
}

func TestNativeLocaleReloadsSavedPreferenceWithoutWriting(t *testing.T) {
	directory := t.TempDir()
	path := filepath.Join(directory, "ui-preferences.json")
	if nativeText(directory, "保存", "Save") != "保存" {
		t.Fatal("missing preferences should use Chinese")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("reading preferences created a file")
	}
	for _, item := range []struct{ contents, want string }{
		{`{"locale":"en"}`, "Save"},
		{`{"locale":"zh-CN"}`, "保存"},
		{`{"locale":"en"}`, "Save"},
		{`{"locale":"EN"}`, "保存"},
		{`{"locale":"fr"}`, "保存"},
		{`{"locale":true}`, "保存"},
		{`[]`, "保存"},
		{`{"locale":"en"`, "保存"},
		{`{"locale":"en"}{}`, "保存"},
		{`{"locale":"en","other":"` + string([]byte{0xff}) + `"}`, "保存"},
		{`{"locale":"en"}` + strings.Repeat(" ", 4096), "保存"},
	} {
		if err := os.WriteFile(path, []byte(item.contents), 0600); err != nil {
			t.Fatal(err)
		}
		if got := nativeText(directory, "保存", "Save"); got != item.want {
			t.Fatalf("locale %q -> %q, want %q", item.contents, got, item.want)
		}
		after, _ := os.ReadFile(path)
		if string(after) != item.contents {
			t.Fatal("native UI changed the preferences file")
		}
	}
}

func TestNativeDiagnosticLocaleIgnoresPersonalDataOverride(t *testing.T) {
	normal, isolated := t.TempDir(), t.TempDir()
	t.Setenv("BIOREADER_DATA_DIR", normal)
	if err := os.WriteFile(filepath.Join(normal, "ui-preferences.json"), []byte(`{"locale":"en"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if serviceDataDirectory("") != normal || serviceDataDirectory(isolated) != isolated {
		t.Fatal("diagnostic data override was not isolated from the environment")
	}
	if nativeText(serviceDataDirectory(isolated), "保存", "Save") != "保存" {
		t.Fatal("diagnostic read the normal application's locale")
	}
}
