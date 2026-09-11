package source

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLangfuseVersionAndURLFailClosed(t *testing.T) {
	for _, version := range []string{"3.175.9", "3.176.1", "4.0.0"} {
		if _, err := NewLangfuseSourceDriver(LangfuseConfig{
			BaseURL: "https://langfuse.example", PublicKey: "pk", SecretKey: "sk", Version: version,
		}); err == nil || !strings.Contains(err.Error(), "schema_incompatible") {
			t.Fatalf("version %s was not rejected: %v", version, err)
		}
	}
	for _, raw := range []string{
		"http://langfuse.example",
		"https://langfuse.example/?query=1",
		"https://pk:sk@langfuse.example",
	} {
		if _, err := NewLangfuseSourceDriver(LangfuseConfig{
			BaseURL: raw, PublicKey: "pk", SecretKey: "sk", Version: LangfuseMinVersion,
		}); err == nil {
			t.Fatalf("unsafe URL was accepted: %s", raw)
		}
	}
}

func TestLangfuseAcquireUsesBothAPIsAndKeepsStreamCompletion(t *testing.T) {
	var observations, scores int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if user, pass, ok := r.BasicAuth(); !ok || user != "pk" || pass != "sk" {
			t.Errorf("unexpected basic auth: %q %q %v", user, pass, ok)
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case LangfuseObservationsPath:
			observations++
			if got := r.URL.Query().Get("fields"); got != "core,basic,time" {
				t.Errorf("observation fields = %q", got)
			}
			if r.URL.Query().Get("cursor") == "" {
				_, _ = w.Write([]byte(`{"data":[{"id":"obs-1","startTime":"2026-09-09T00:00:00Z","traceId":"trace-1","input":"must-not-cross"}],"meta":{"cursor":"obs-next"}}`))
			} else {
				_, _ = w.Write([]byte(`{"data":[{"id":"obs-2","startTime":"2026-09-09T00:00:01Z","traceId":"trace-1"}],"meta":{"cursor":null}}`))
			}
		case LangfuseScoresPath:
			scores++
			if got := r.URL.Query().Get("fields"); got != "core" {
				t.Errorf("score fields = %q; Langfuse 4.32.0 accepts core, details, subject, or annotation", got)
			}
			if r.URL.Query().Get("cursor") == "" {
				_, _ = w.Write([]byte(`{"data":[{"id":"score-1","timestamp":"2026-09-09T00:00:02Z","name":"quality","value":0.75,"dataType":"NUMERIC","source":"EVAL"}],"meta":{"limit":1,"cursor":"score-next"}}`))
			} else {
				_, _ = w.Write([]byte(`{"data":[{"id":"score-2","timestamp":"2026-09-09T00:00:03Z","name":"quality","value":true,"dataType":"BOOLEAN","source":"ANNOTATION"}],"meta":{"limit":1,"cursor":null}}`))
			}
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	driver, err := NewLangfuseSourceDriver(LangfuseConfig{BaseURL: server.URL, PublicKey: "pk", SecretKey: "sk", Version: LangfuseMinVersion})
	if err != nil {
		t.Fatal(err)
	}
	first, checkpoint, err := driver.Acquire(context.Background(), "", 2)
	if err != nil || len(first) != 2 {
		t.Fatalf("first acquire: records=%+v checkpoint=%q err=%v", first, checkpoint, err)
	}
	score, err := driver.Normalize(first[1])
	if err != nil || score.Attributes["langfuse.value"] != 0.75 || score.Attributes["langfuse.dataType"] != "NUMERIC" {
		t.Fatalf("fields=core score normalization: score=%+v err=%v", score, err)
	}
	cursor, err := decodeLangfuseCursor(checkpoint)
	if err != nil || cursor.Observation != "obs-next" || cursor.ObservationDone || cursor.ScoreCursor != "score-next" || cursor.ScoreDone {
		t.Fatalf("first cursor=%+v err=%v", cursor, err)
	}
	second, checkpoint, err := driver.Acquire(context.Background(), checkpoint, 2)
	if err != nil || len(second) != 2 {
		t.Fatalf("second acquire: records=%+v checkpoint=%q err=%v", second, checkpoint, err)
	}
	cursor, err = decodeLangfuseCursor(checkpoint)
	if err != nil || !cursor.ObservationDone || cursor.ScoreCursor != "" || !cursor.ScoreDone {
		t.Fatalf("second cursor=%+v err=%v", cursor, err)
	}
	third, _, err := driver.Acquire(context.Background(), checkpoint, 2)
	if err != nil || len(third) != 0 {
		t.Fatalf("third acquire: records=%+v err=%v", third, err)
	}
	if observations != 2 || scores != 2 {
		t.Fatalf("API calls observations=%d scores=%d; completed streams must not restart", observations, scores)
	}
}

func TestLangfuseNormalizeIsAllowlistIn(t *testing.T) {
	driver, err := NewLangfuseSourceDriver(LangfuseConfig{BaseURL: "https://langfuse.example", PublicKey: "pk", SecretKey: "sk", Version: LangfuseMinVersion})
	if err != nil {
		t.Fatal(err)
	}
	normalized, err := driver.Normalize(RawEnvelope{Payload: map[string]any{
		"_backbond_kind": "observation", "id": "obs-1", "startTime": "2026-09-09T00:00:00Z",
		"traceId": "trace-1", "input": "secret-content", "metadata": map[string]any{"email": "redacted"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if normalized.Attributes["schema_event_type"] != "agent_invocation" || normalized.SourceIntegrity["observations_path"] != LangfuseObservationsPath {
		t.Fatalf("normalized=%+v", normalized)
	}
	if _, ok := normalized.Attributes["langfuse.input"]; ok {
		t.Fatal("input crossed the allowlist")
	}
	if _, ok := normalized.Attributes["langfuse.metadata"]; ok {
		t.Fatal("metadata crossed the allowlist")
	}

	score, err := driver.Normalize(RawEnvelope{Payload: map[string]any{
		"_backbond_kind": "score", "id": "score-1", "timestamp": "2026-09-09T00:00:00Z",
		"value": 0.5, "comment": "free text must not cross", "traceId": "trace-1",
	}})
	if err != nil || score.Attributes["schema_event_type"] != "claim_candidate_event" || score.Attributes["langfuse.value"] != 0.5 {
		t.Fatalf("score=%+v err=%v", score, err)
	}
}

func TestLangfuseHTTPFailuresAreTyped(t *testing.T) {
	for _, tc := range []struct {
		status int
		kind   FailureKind
	}{
		{http.StatusUnauthorized, FailureAuth},
		{http.StatusForbidden, FailureAuth},
		{http.StatusTooManyRequests, FailureRateLimit},
		{http.StatusBadGateway, FailureTransient},
		{http.StatusBadRequest, FailureSchema},
	} {
		t.Run(http.StatusText(tc.status), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(tc.status) }))
			driver, err := NewLangfuseSourceDriver(LangfuseConfig{BaseURL: server.URL, PublicKey: "pk", SecretKey: "sk", Version: LangfuseMinVersion})
			if err != nil {
				t.Fatal(err)
			}
			_, _, err = driver.Acquire(context.Background(), "", 1)
			server.Close()
			var sourceErr *Error
			if !errors.As(err, &sourceErr) || sourceErr.Kind != tc.kind {
				t.Fatalf("err=%v typed=%+v want=%s", err, sourceErr, tc.kind)
			}
		})
	}
}

func TestLangfuseCheckpointRejectsInvalidValues(t *testing.T) {
	for _, value := range []string{"!", base64.RawURLEncoding.EncodeToString([]byte(`{"score_done":true,"score_cursor":"next"}`))} {
		if _, err := decodeLangfuseCursor(value); err == nil {
			t.Fatalf("checkpoint accepted %q", value)
		}
	}
}

func TestLangfuseResponseMissingDataFailsClosed(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"meta":{}}`)) }))
	defer server.Close()
	driver, err := NewLangfuseSourceDriver(LangfuseConfig{BaseURL: server.URL, PublicKey: "pk", SecretKey: "sk", Version: LangfuseMinVersion})
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = driver.Acquire(context.Background(), "", 1)
	var sourceErr *Error
	if !errors.As(err, &sourceErr) || sourceErr.Kind != FailureSchema {
		t.Fatalf("err=%v typed=%+v", err, sourceErr)
	}
}

func TestLangfuseCheckpointRoundTrip(t *testing.T) {
	cursor := langfuseCursor{Observation: "abc", ObservationDone: true, ScoreDone: true}
	b, err := json.Marshal(cursor)
	if err != nil {
		t.Fatal(err)
	}
	encoded := base64.RawURLEncoding.EncodeToString(b)
	got, err := decodeLangfuseCursor(encoded)
	if err != nil || got != cursor {
		t.Fatalf("got=%+v want=%+v err=%v", got, cursor, err)
	}
}
