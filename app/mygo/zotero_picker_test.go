package main

import "testing"

func TestLocalZoteroItemURLs(t *testing.T) {
	for _, value := range []string{"zotero://select/library/items/ZXT123AB", "zotero://select/groups/245/items/28QC3RB5"} {
		if !zoteroItemURL.MatchString(value) {
			t.Fatal(value)
		}
	}
	for _, value := range []string{"https://example.org", "zotero://open-pdf/library/items/ZXT123AB",
		"zotero://select/library/items/ZXT123AB?command=1", "zotero://select/groups/0/items/ZXT123AB", "zotero://select/library/items/../ZXT123AB"} {
		if zoteroItemURL.MatchString(value) {
			t.Fatal(value)
		}
	}
}
