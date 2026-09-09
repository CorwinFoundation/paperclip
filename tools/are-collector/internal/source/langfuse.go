package source

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	LangfuseDriverVersion    = "langfuse-source-v0.1"
	LangfuseMappingVersion   = "langfuse-map-v0.1"
	LangfuseMinVersion       = "4.32.0"
	LangfuseMaxVersion       = "4.32.0"
	LangfuseOpenAPISHA256    = "df0d9d24126017db3ae0f5cc4bc2f169df73d0b11cc31d8b52c1c25ae7b6326a"
	LangfuseObservationsPath = "/api/public/v2/observations"
	LangfuseScoresPath       = "/api/public/v3/scores"
)

type LangfuseConfig struct {
	BaseURL, PublicKey, SecretKey, Version string
	Client                                 *http.Client
}
type LangfuseSourceDriver struct {
	base                             *url.URL
	publicKey, secretKey, checkpoint string
	client                           *http.Client
}
type langfuseCursor struct {
	Observation     string `json:"observation,omitempty"`
	ObservationDone bool   `json:"observation_done,omitempty"`
	ScoreCursor     string `json:"score_cursor,omitempty"`
	ScoreDone       bool   `json:"score_done,omitempty"`
}

func NewLangfuseSourceDriver(cfg LangfuseConfig) (*LangfuseSourceDriver, error) {
	if cfg.Version < LangfuseMinVersion || cfg.Version > LangfuseMaxVersion {
		return nil, fmt.Errorf("schema_incompatible: langfuse %q outside supported range %s..%s", cfg.Version, LangfuseMinVersion, LangfuseMaxVersion)
	}
	u, err := url.Parse(cfg.BaseURL)
	if err != nil || u.Scheme == "" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("invalid langfuse base URL")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost")) {
		return nil, fmt.Errorf("langfuse base URL must use https (http allowed only on loopback)")
	}
	if cfg.PublicKey == "" || cfg.SecretKey == "" {
		return nil, fmt.Errorf("langfuse scoped credentials are required")
	}
	c := cfg.Client
	if c == nil {
		c = &http.Client{Timeout: 20 * time.Second}
	}
	clone := *c
	clone.CheckRedirect = func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }
	return &LangfuseSourceDriver{base: u, publicKey: cfg.PublicKey, secretKey: cfg.SecretKey, client: &clone}, nil
}

func (d *LangfuseSourceDriver) Descriptor() Descriptor {
	return Descriptor{DriverID: "langfuse", DriverVersion: LangfuseDriverVersion, SourceClass: "llm_observability", SourcePin: LangfuseMinVersion, EvidenceQuality: EvidenceGateway}
}
func (d *LangfuseSourceDriver) Checkpoint(context.Context) (string, error) { return d.checkpoint, nil }
func (d *LangfuseSourceDriver) Commit(_ context.Context, checkpoint string) error {
	if _, err := decodeLangfuseCursor(checkpoint); err != nil {
		return err
	}
	d.checkpoint = checkpoint
	return nil
}

func (d *LangfuseSourceDriver) Acquire(ctx context.Context, after string, max int) ([]RawEnvelope, string, error) {
	if max <= 0 || max > 100 {
		max = 100
	}
	cursor, err := decodeLangfuseCursor(after)
	if err != nil {
		return nil, "", err
	}
	obs := []map[string]any{}
	nextObs := ""
	if !cursor.ObservationDone {
		obsQ := url.Values{"fields": {"core,basic,time"}, "limit": {strconv.Itoa(max)}}
		if cursor.Observation != "" {
			obsQ.Set("cursor", cursor.Observation)
		}
		obs, nextObs, err = d.get(ctx, LangfuseObservationsPath, obsQ)
		if err != nil {
			return nil, "", err
		}
		cursor.Observation = nextObs
		cursor.ObservationDone = nextObs == ""
	}
	remaining := max - len(obs)
	scores := []map[string]any{}
	if remaining > 0 && !cursor.ScoreDone {
		q := url.Values{"fields": {"core,score"}, "limit": {strconv.Itoa(remaining)}}
		if cursor.ScoreCursor != "" {
			q.Set("cursor", cursor.ScoreCursor)
		}
		scores, cursor.ScoreCursor, err = d.getScores(ctx, q)
		if err != nil {
			return nil, "", err
		}
		cursor.ScoreDone = cursor.ScoreCursor == ""
	}
	records := make([]RawEnvelope, 0, len(obs)+len(scores))
	for _, v := range obs {
		v["_backbond_kind"] = "observation"
		records = append(records, RawEnvelope{Offset: stringValue(v["id"]), Payload: v})
	}
	for _, v := range scores {
		v["_backbond_kind"] = "score"
		records = append(records, RawEnvelope{Offset: stringValue(v["id"]), Payload: v})
	}
	next, _ := json.Marshal(cursor)
	return records, base64.RawURLEncoding.EncodeToString(next), nil
}

func (d *LangfuseSourceDriver) get(ctx context.Context, path string, q url.Values) ([]map[string]any, string, error) {
	var body struct {
		Data []map[string]any `json:"data"`
		Meta map[string]any   `json:"meta"`
	}
	if err := d.request(ctx, path, q, &body); err != nil {
		return nil, "", err
	}
	if body.Data == nil {
		return nil, "", &Error{Kind: FailureSchema, Err: fmt.Errorf("langfuse response missing data array")}
	}
	return body.Data, stringValue(body.Meta["cursor"]), nil
}
func (d *LangfuseSourceDriver) getScores(ctx context.Context, q url.Values) ([]map[string]any, string, error) {
	var body struct {
		Data []map[string]any `json:"data"`
		Meta map[string]any   `json:"meta"`
	}
	if err := d.request(ctx, LangfuseScoresPath, q, &body); err != nil {
		return nil, "", err
	}
	if body.Data == nil {
		return nil, "", &Error{Kind: FailureSchema, Err: fmt.Errorf("langfuse scores response missing data array")}
	}
	return body.Data, stringValue(body.Meta["cursor"]), nil
}
func (d *LangfuseSourceDriver) request(ctx context.Context, path string, q url.Values, out any) error {
	u := *d.base
	u.Path = strings.TrimRight(u.Path, "/") + path
	u.RawQuery = q.Encode()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	req.SetBasicAuth(d.publicKey, d.secretKey)
	resp, err := d.client.Do(req)
	if err != nil {
		return &Error{Kind: FailureTransient, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return &Error{Kind: FailureSchema, Err: fmt.Errorf("langfuse redirect refused")}
	}
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return &Error{Kind: FailureAuth, Err: fmt.Errorf("auth_denied: langfuse rejected scoped credentials")}
	}
	if resp.StatusCode == 429 {
		return &Error{Kind: FailureRateLimit, Err: fmt.Errorf("rate_limited: langfuse rate limit")}
	}
	if resp.StatusCode != 200 {
		kind := FailureSchema
		if resp.StatusCode >= 500 {
			kind = FailureTransient
		}
		return &Error{Kind: kind, Err: fmt.Errorf("langfuse HTTP %d", resp.StatusCode)}
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return &Error{Kind: FailureSchema, Err: fmt.Errorf("langfuse response: %w", err)}
	}
	return nil
}

// Normalize is deliberately allowlist-in: prompt/output/metadata/tool arguments never cross this boundary.
func (d *LangfuseSourceDriver) Normalize(raw RawEnvelope) (NormalizedEvent, error) {
	p := raw.Payload
	kind := stringValue(p["_backbond_kind"])
	if kind != "observation" && kind != "score" {
		return NormalizedEvent{}, fmt.Errorf("langfuse unsupported record kind %q", kind)
	}
	id := stringValue(p["id"])
	if id == "" {
		return NormalizedEvent{}, fmt.Errorf("langfuse %s missing id", kind)
	}
	observed := stringValue(p["startTime"])
	if kind == "score" {
		observed = stringValue(p["timestamp"])
	}
	if observed == "" {
		return NormalizedEvent{}, fmt.Errorf("langfuse %s missing timestamp", kind)
	}
	if _, err := time.Parse(time.RFC3339, observed); err != nil {
		return NormalizedEvent{}, fmt.Errorf("langfuse %s timestamp must be RFC3339: %w", kind, err)
	}
	attrs := map[string]any{"schema_event_type": "agent_invocation", "langfuse.kind": kind}
	for _, k := range []string{"type", "name", "level", "source", "dataType", "traceId", "observationId", "sessionId"} {
		if v, ok := p[k]; ok {
			attrs["langfuse."+k] = v
		}
	}
	op := "observe"
	action := "message"
	billable := false
	if kind == "score" {
		op = "evaluate_agent"
		action = "evaluation"
		attrs["schema_event_type"] = "claim_candidate_event"
		if value, ok := safeLangfuseScalar(p["value"]); ok {
			attrs["langfuse.value"] = value
		}
	}
	return NormalizedEvent{SourceEventID: "langfuse-" + kind + "-" + id, ObservedAt: observed, SessionID: stringValue(p["sessionId"]), OperationName: op, ActionClass: action, Billable: billable, OutcomeStatus: stringValue(p["level"]), Attributes: attrs, SourceIntegrity: map[string]any{"openapi_sha256": LangfuseOpenAPISHA256, "source_version": LangfuseMinVersion, "observations_path": LangfuseObservationsPath, "scores_path": LangfuseScoresPath}, MappingVersion: LangfuseMappingVersion}, nil
}

func safeLangfuseScalar(value any) (any, bool) {
	switch value.(type) {
	case string, bool, float64, float32, int, int64, uint64:
		return value, true
	default:
		return nil, false
	}
}
func decodeLangfuseCursor(s string) (langfuseCursor, error) {
	if s == "" {
		return langfuseCursor{}, nil
	}
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return langfuseCursor{}, fmt.Errorf("invalid langfuse checkpoint")
	}
	var c langfuseCursor
	if json.Unmarshal(b, &c) != nil || (c.ScoreDone && c.ScoreCursor != "") {
		return langfuseCursor{}, fmt.Errorf("invalid langfuse checkpoint")
	}
	return c, nil
}
