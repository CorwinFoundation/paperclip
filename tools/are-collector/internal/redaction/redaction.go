package redaction

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"backbond/are-collector/internal/schemav1"
)

type Class string

const (
	Clear           Class = "clear"
	Hash            Class = "hash"
	RedactOnReceipt Class = "redact-on-receipt"
	Drop            Class = "drop"
)

type Policy map[string]Class

var secretPatterns = []*regexp.Regexp{
	regexp.MustCompile(`AKIA[0-9A-Z]{16}`),
	regexp.MustCompile(`(?i)sk-[a-z0-9_-]{20,}`),
	regexp.MustCompile(`(?i)(api[_-]?key|secret|token)\s*[:=]\s*['"]?[^'"\s]{12,}`),
	regexp.MustCompile(`(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----`),
	regexp.MustCompile(`eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}`),
	regexp.MustCompile(`(?i)(aws[_-]?secret[_-]?(?:access[_-]?)?key)\s*[:=]\s*['"]?[A-Za-z0-9/+=]{40}`),
	regexp.MustCompile(`sv=\d{4}-\d{2}-\d{2}[^#\s]*?&sig=[A-Za-z0-9%+/=]{20,}`),
}

var piiPatterns = map[string]*regexp.Regexp{
	"email": regexp.MustCompile(`(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b`),
	"phone": regexp.MustCompile(`\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b`),
	"ssn":   regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`),
}

func Apply(event schemav1.Event, policy Policy) (schemav1.Event, error) {
	payload := cloneMap(event.Payload)
	applied := map[string]struct{}{}
	applySecretFloor(payload, "", applied)

	paths := make([]string, 0, len(policy))
	for path := range policy {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	for _, path := range paths {
		class := policy[path]
		if err := applyPath(payload, strings.Split(path, "."), class); err != nil {
			return event, err
		}
		if hasPath(payload, strings.Split(path, ".")) || class == Drop {
			applied[path] = struct{}{}
		}
	}

	event.Payload = payload
	event.RedactionApplied = sortedKeys(applied)
	return event, nil
}

func applySecretFloor(value any, path string, applied map[string]struct{}) {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			nextPath := key
			if path != "" {
				nextPath = path + "." + key
			}
			if raw, ok := child.(string); ok {
				redacted := raw
				for _, pattern := range secretPatterns {
					redacted = pattern.ReplaceAllString(redacted, "[REDACTED:secret_family]")
				}
				if redacted != raw {
					typed[key] = redacted
					applied[nextPath] = struct{}{}
				}
				continue
			}
			applySecretFloor(child, nextPath, applied)
		}
	case []any:
		for idx, child := range typed {
			nextPath := fmt.Sprintf("%s[%d]", path, idx)
			applySecretFloor(child, nextPath, applied)
		}
	}
}

func applyPath(value map[string]any, parts []string, class Class) error {
	if len(parts) == 0 || parts[0] == "" {
		return fmt.Errorf("redaction path must be non-empty")
	}
	if len(parts) == 1 {
		return applyField(value, parts[0], class)
	}
	child, ok := value[parts[0]].(map[string]any)
	if !ok {
		return nil
	}
	return applyPath(child, parts[1:], class)
}

func applyField(container map[string]any, field string, class Class) error {
	original, ok := container[field]
	if !ok {
		return nil
	}
	switch class {
	case Clear:
		return nil
	case Hash:
		canonical, err := schemav1.CanonicalJSON(original)
		if err != nil {
			return err
		}
		digest := sha256.Sum256(canonical)
		container[field] = "sha256:" + hex.EncodeToString(digest[:])
	case RedactOnReceipt:
		if raw, ok := original.(string); ok {
			container[field] = redactPII(raw)
		} else {
			container[field] = "[REDACTED:value]"
		}
	case Drop:
		delete(container, field)
	default:
		return fmt.Errorf("unsupported redaction class: %q", class)
	}
	return nil
}

func redactPII(value string) string {
	redacted := value
	keys := make([]string, 0, len(piiPatterns))
	for key := range piiPatterns {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		redacted = piiPatterns[key].ReplaceAllString(redacted, "[REDACTED:"+key+"]")
	}
	return redacted
}

func hasPath(value map[string]any, parts []string) bool {
	if len(parts) == 0 || parts[0] == "" {
		return false
	}
	if len(parts) == 1 {
		_, ok := value[parts[0]]
		return ok
	}
	child, ok := value[parts[0]].(map[string]any)
	if !ok {
		return false
	}
	return hasPath(child, parts[1:])
}

func cloneMap(in map[string]any) map[string]any {
	out := make(map[string]any, len(in))
	for key, value := range in {
		switch typed := value.(type) {
		case map[string]any:
			out[key] = cloneMap(typed)
		case []any:
			copied := make([]any, len(typed))
			for idx, item := range typed {
				if child, ok := item.(map[string]any); ok {
					copied[idx] = cloneMap(child)
				} else {
					copied[idx] = item
				}
			}
			out[key] = copied
		default:
			out[key] = typed
		}
	}
	return out
}

func sortedKeys(values map[string]struct{}) []string {
	out := make([]string, 0, len(values))
	for value := range values {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}
