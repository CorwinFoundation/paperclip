package tests

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var forbiddenImportFragments = []string{
	"bands",
	"portal",
	"pricing",
	"rubric",
	"scoring",
	"scanner",
	"post-bind-ingest",
	"post_bind_ingest",
	"tools/portal_v1",
}

func TestCollectorDoesNotImportScoringOrReceiverCode(t *testing.T) {
	root := filepath.Join("..", "internal")
	offenders := []string{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		for _, node := range file.Imports {
			importPath := strings.Trim(node.Path.Value, `"`)
			for _, fragment := range forbiddenImportFragments {
				if strings.Contains(importPath, fragment) {
					offenders = append(offenders, path+":"+importPath)
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) != 0 {
		t.Fatalf("forbidden imports: %#v", offenders)
	}
	_ = ast.Package{}
}
