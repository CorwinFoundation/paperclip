package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	"backbond/are-collector/internal/collector"
	"backbond/are-collector/internal/source"
)

func main() {
	var records []map[string]any
	if err := json.NewDecoder(os.Stdin).Decode(&records); err != nil {
		fail(err)
	}
	driver := source.NewSyntheticVoiceDriver(records)
	events, err := (collector.Pipeline{}).CollectDriver(context.Background(), driver, 0)
	if err != nil {
		fail(err)
	}
	out := struct {
		Descriptor source.Descriptor `json:"descriptor"`
		Events     any               `json:"events"`
	}{driver.Descriptor(), events}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fail(err)
	}
}
func fail(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
