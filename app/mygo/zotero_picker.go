package main

import (
	"context"
	"errors"
	"regexp"

	"github.com/egoist/mygo"
)

// The webview bridge only accepts calls from the configured service origin.
type zoteroPicker struct{ dataDirectory string }

func (p *zoteroPicker) Choose(ctx context.Context, folder bool) (string, error) {
	options := mygo.OpenDialogOptions{Parent: mygo.CallerWindow(ctx), Directory: folder,
		Title: nativeText(p.dataDirectory, "选择 Zotero 资料目录", "Choose a Zotero data directory")}
	if !folder {
		options.Title = nativeText(p.dataDirectory, "选择 zotero.sqlite", "Choose zotero.sqlite")
		options.Filters = []mygo.FileFilter{{Name: "Zotero SQLite", Extensions: []string{"sqlite"}}}
	}
	paths, err := mygo.Dialog.Open(options)
	if len(paths) == 0 || err != nil {
		return "", err
	}
	return paths[0], nil
}

var zoteroItemURL = regexp.MustCompile(`^zotero://select/(library|groups/[1-9][0-9]*)/items/[A-Z0-9]{8}$`)

func (p *zoteroPicker) ShowItem(uri string) error {
	if !zoteroItemURL.MatchString(uri) {
		return errors.New(nativeText(p.dataDirectory, "Zotero 条目地址无效", "Invalid Zotero item URL"))
	}
	return mygo.Shell.OpenExternal(uri)
}
