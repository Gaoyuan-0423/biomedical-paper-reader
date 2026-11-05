package main

import (
	"errors"
	"testing"
)

func TestNativeWindowRequiresExplicitDiagnosticCompletion(t *testing.T) {
	t.Run("early exit does not pass", func(t *testing.T) {
		result := make(chan error, 1)
		if completedWindowResult(result, true) == nil {
			t.Fatal("unfinished native diagnostic was reported as successful")
		}
		// A late completion cannot change the error already returned above.
		result <- nil
	})
	t.Run("completed checks pass", func(t *testing.T) {
		result := make(chan error, 1)
		result <- nil
		if err := completedWindowResult(result, true); err != nil {
			t.Fatalf("completed native diagnostic failed: %v", err)
		}
	})
	t.Run("failed check is preserved", func(t *testing.T) {
		result := make(chan error, 1)
		failure := errors.New("figure observation did not render")
		result <- failure
		if err := completedWindowResult(result, true); !errors.Is(err, failure) {
			t.Fatalf("native failure was lost: %v", err)
		}
	})
	t.Run("ordinary window may close normally", func(t *testing.T) {
		if err := completedWindowResult(make(chan error, 1), false); err != nil {
			t.Fatalf("normal window closure changed: %v", err)
		}
	})
}
