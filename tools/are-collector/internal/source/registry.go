package source

import (
	"fmt"
	"io"
	"os"
	"sort"
)

type Factory func(io.Reader) (SourceDriver, error)

type Registry struct{ factories map[string]Factory }

type RegistryConfig struct {
	NumbatCommit, NumbatSchema string
	Langfuse                   LangfuseConfig
	Datadog                    DatadogConfig
}

func NewRegistry() *Registry { return &Registry{factories: map[string]Factory{}} }

func (r *Registry) Register(id string, factory Factory) error {
	if id == "" || factory == nil {
		return fmt.Errorf("source id and factory are required")
	}
	if _, exists := r.factories[id]; exists {
		return fmt.Errorf("source driver %q already registered", id)
	}
	r.factories[id] = factory
	return nil
}

func (r *Registry) Resolve(id string, input io.Reader) (SourceDriver, error) {
	factory, ok := r.factories[id]
	if !ok {
		return nil, fmt.Errorf("unknown source driver %q (registered: %v)", id, r.IDs())
	}
	return factory(input)
}

func (r *Registry) IDs() []string {
	ids := make([]string, 0, len(r.factories))
	for id := range r.factories {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

func DefaultRegistry() *Registry {
	r := NewRegistry()
	_ = r.Register("numbat", func(input io.Reader) (SourceDriver, error) { return NewNumbatSourceDriver(input) })
	_ = r.RegisterDatadog(DatadogConfig{
		Site:           os.Getenv("DATADOG_SITE"),
		APIKey:         os.Getenv("DD_API_KEY"),
		ApplicationKey: os.Getenv("DD_APP_KEY"),
		Query:          os.Getenv("DATADOG_QUERY"),
	})
	return r
}

func ConfiguredRegistry(cfg RegistryConfig) *Registry {
	r := NewRegistry()
	_ = r.Register("numbat", func(input io.Reader) (SourceDriver, error) {
		if cfg.NumbatCommit != NumbatCommit {
			return nil, fmt.Errorf("numbat commit pin mismatch: got %q, require %q", cfg.NumbatCommit, NumbatCommit)
		}
		if cfg.NumbatSchema != NumbatSchemaVersion {
			return nil, fmt.Errorf("numbat schema pin mismatch: got %q, require %q", cfg.NumbatSchema, NumbatSchemaVersion)
		}
		return NewNumbatSourceDriver(input)
	})
	_ = r.RegisterLangfuse(cfg.Langfuse)
	if cfg.Datadog.Site == "" && cfg.Datadog.APIKey == "" && cfg.Datadog.ApplicationKey == "" && cfg.Datadog.Query == "" && cfg.Datadog.Client == nil {
		cfg.Datadog = datadogConfigFromEnvironment()
	}
	_ = r.RegisterDatadog(cfg.Datadog)
	return r
}

func datadogConfigFromEnvironment() DatadogConfig {
	return DatadogConfig{
		Site:           os.Getenv("DATADOG_SITE"),
		APIKey:         os.Getenv("DD_API_KEY"),
		ApplicationKey: os.Getenv("DD_APP_KEY"),
		Query:          os.Getenv("DATADOG_QUERY"),
	}
}

func (r *Registry) RegisterLangfuse(cfg LangfuseConfig) error {
	return r.Register("langfuse", func(io.Reader) (SourceDriver, error) {
		return NewLangfuseSourceDriver(cfg)
	})
}

func (r *Registry) RegisterDatadog(cfg DatadogConfig) error {
	return r.Register("datadog", func(io.Reader) (SourceDriver, error) {
		return NewDatadogSourceDriver(cfg)
	})
}
