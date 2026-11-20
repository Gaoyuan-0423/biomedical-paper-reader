package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/http"
	"strings"
)

// Exercise python-pptx's packaged notes-master templates using an actual stored figure.
func exerciseFigureDeck(client *http.Client, origin, paperID, figureID string) error {
	figure := origin + "/api/papers/" + paperID + "/figures/" + figureID
	png, err := requestBytes(client, "GET", figure+".png", "", nil, 200)
	if err != nil {
		return err
	}
	data, err := requestBytes(client, "PATCH", figure, "application/json", []byte(`{"favorite":true}`), 200)
	if err != nil {
		return err
	}
	var favorite struct {
		Key string `json:"shelf_key"`
	}
	if err := json.Unmarshal(data, &favorite); err != nil {
		return err
	}
	slide := map[string]string{"paper_id": paperID, "id": figureID, "shelf_key": favorite.Key}
	ref, _ := json.Marshal(slide)
	data, err = requestBytes(client, "POST", origin+"/api/figure-decks/all/preview", "application/json", ref, 200)
	if err != nil {
		return err
	}
	var preview struct {
		Revision string `json:"revision"`
	}
	if err := json.Unmarshal(data, &preview); err != nil {
		return err
	}
	slide["revision"] = preview.Revision
	slide["title"] = "Synthetic journal club"
	slide["question"] = "Which specimens were collected?"
	slide["control"] = "An independent comparison group"
	slide["observation"] = "The caption describes sample collection"
	slide["limitation"] = "Only one synthetic figure was reviewed"
	slide["notes"] = "核对每组样本的来源。"
	body, _ := json.Marshal(map[string][]map[string]string{"slides": {slide}})
	data, err = requestBytes(client, "POST", origin+"/api/figure-decks/all/pptx", "application/json", body, 200)
	if err != nil {
		return err
	}
	deck, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return err
	}
	for path, want := range map[string]string{
		"ppt/slides/slide1.xml": slide["title"], "ppt/notesSlides/notesSlide1.xml": slide["notes"],
		"ppt/notesMasters/notesMaster1.xml": "notesMaster",
	} {
		part, err := fs.ReadFile(deck, path)
		if err != nil {
			return err
		}
		if !bytes.Contains(part, []byte(want)) {
			return fmt.Errorf("missing expected content in %s", path)
		}
	}
	for _, file := range deck.File {
		if strings.HasPrefix(file.Name, "ppt/media/") {
			part, err := fs.ReadFile(deck, file.Name)
			if err != nil {
				return err
			}
			if bytes.Equal(part, png) {
				return nil
			}
		}
	}
	return errors.New("PowerPoint did not contain the exported figure")
}
