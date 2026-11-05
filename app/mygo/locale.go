package main

import (
	"encoding/json"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"unicode/utf8"
)

// Match app/desktop_paths.py. Never create or update a library from native UI.
func desktopDataDirectory(platform, home string, getenv func(string) string) string {
	if explicit := getenv("BIOREADER_DATA_DIR"); explicit != "" {
		if explicit[0] != '~' {
			return explicit
		}
		separators := "/"
		if platform == "windows" {
			separators += `\`
		}
		end := strings.IndexAny(explicit, separators)
		if end < 0 {
			end = len(explicit)
		}
		name, base := explicit[1:end], home
		if name != "" {
			if platform == "windows" {
				// Python's ntpath.expanduser supports sibling profiles only when
				// USERNAME matches the basename of the current profile directory.
				username := getenv("USERNAME")
				if name != username {
					if username == "" || filepath.Base(home) != username {
						return ""
					}
					base = filepath.Join(filepath.Dir(home), name)
				}
			} else {
				account, err := user.Lookup(name)
				if err != nil {
					return ""
				}
				base = account.HomeDir
			}
		}
		if base == "" {
			return ""
		}
		return filepath.Join(base, strings.TrimLeft(explicit[end:], separators))
	}
	if home == "" {
		return ""
	}
	switch platform {
	case "windows":
		base := getenv("LOCALAPPDATA")
		if base == "" {
			base = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(base, "BioReader")
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "BioReader")
	default:
		base := getenv("XDG_DATA_HOME")
		if base == "" {
			base = filepath.Join(home, ".local", "share")
		}
		return filepath.Join(base, "BioReader")
	}
}

func nativeDataDirectory() string {
	home, _ := os.UserHomeDir()
	return desktopDataDirectory(runtime.GOOS, home, os.Getenv)
}

func serviceDataDirectory(diagnosticData string) string {
	if diagnosticData != "" {
		return diagnosticData
	}
	return nativeDataDirectory()
}

func readNativeLocale(directory string) string {
	if directory == "" {
		return "zh-CN"
	}
	file, err := os.Open(filepath.Join(directory, "ui-preferences.json"))
	if err != nil {
		return "zh-CN"
	}
	defer file.Close()
	// Preference failures must not hide a startup error or modify the file.
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
		return "zh-CN"
	}
	data, err := io.ReadAll(io.LimitReader(file, 4097))
	var preferences struct {
		Locale string `json:"locale"`
	}
	if err == nil && len(data) <= 4096 && utf8.Valid(data) && json.Unmarshal(data, &preferences) == nil && preferences.Locale == "en" {
		return "en"
	}
	return "zh-CN"
}

func nativeText(directory, chinese, english string) string {
	if readNativeLocale(directory) == "en" {
		return english
	}
	return chinese
}
