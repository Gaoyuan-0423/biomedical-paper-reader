package main

import "testing"

func TestServiceReadinessRequiresExactLoopbackOrigin(t *testing.T) {
	for _, raw := range []string{"http://127.0.0.1:12345", "http://127.0.0.1:65535"} {
		if !validServiceURL(raw) {
			t.Fatalf("valid service URL rejected: %s", raw)
		}
	}
	for _, raw := range []string{"https://127.0.0.1:12345", "http://localhost:12345", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:12345/other", "http://127.0.0.1:12345?token=secret", "http://user@127.0.0.1:12345", "http://127.0.0.1.evil:12345"} {
		if validServiceURL(raw) {
			t.Fatalf("unsafe service URL accepted: %s", raw)
		}
	}
}

func TestExternalNavigationDoesNotExposeLocalSession(t *testing.T) {
	origin := "http://127.0.0.1:12345"
	if !sameOrigin(origin+"/api/papers", origin) {
		t.Fatal("local API rejected")
	}
	for _, raw := range []string{"http://127.0.0.1:12346/", "http://127.0.0.1:12345.evil/", "https://127.0.0.1:12345/", "http://user@127.0.0.1:12345/"} {
		if sameOrigin(raw, origin) {
			t.Fatalf("foreign origin accepted: %s", raw)
		}
	}
	for _, raw := range []string{"file:///etc/passwd", "javascript:alert(1)", "ms-settings:privacy", "https://user:secret@example.com/", "//example.com/"} {
		if externalURL(raw) {
			t.Fatalf("unsafe external URL accepted: %s", raw)
		}
	}
	if !externalURL("https://www.ncbi.nlm.nih.gov/geo/") {
		t.Fatal("research link rejected")
	}
}
