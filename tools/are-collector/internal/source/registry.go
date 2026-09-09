package source

import (
	"fmt"
	"io"
	"sort"
)

type Factory func(io.Reader) (SourceDriver, error)

type Registry struct{ factories map[string]Factory }

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
	return r
}
