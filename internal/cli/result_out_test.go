package cli

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type failingResultOutTemp struct {
	resultOutTemp
	writeErr error
	closeErr error
}

func (f failingResultOutTemp) Write(p []byte) (int, error) {
	if f.writeErr != nil {
		return 0, f.writeErr
	}
	return f.resultOutTemp.Write(p)
}

func (f failingResultOutTemp) Close() error {
	err := f.resultOutTemp.Close()
	if f.closeErr != nil {
		return f.closeErr
	}
	return err
}

// --result-out writes identical bytes to stdout to the file, mode 0600.
func TestRunResultFormatJSONResultOutToFile(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	f := &fakeTr{rc: 0}
	outFile := filepath.Join(t.TempDir(), "result.json")
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--result-format=json", "--result-out", outFile, "web01", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	data, err := os.ReadFile(outFile)
	if err != nil {
		t.Fatalf("read result-out: %v", err)
	}
	if string(data) != out.String() {
		t.Fatalf("result-out != stdout\nstdout: %s\nfile: %s", out.String(), data)
	}
	if fi, _ := os.Stat(outFile); fi.Mode().Perm() != 0o600 {
		t.Fatalf("mode=%o, want 600", fi.Mode().Perm())
	}
}

// Existing --result-out files are atomically replaced by one private JSON
// document rather than appended, including when their prior mode was unsafe.
func TestRunResultFormatJSONResultOutReplacesExistingFile(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	outFile := filepath.Join(t.TempDir(), "result.json")
	if err := os.WriteFile(outFile, []byte("existing\n"), 0o644); err != nil {
		t.Fatalf("seed result-out: %v", err)
	}
	if err := os.Chmod(outFile, 0o644); err != nil {
		t.Fatalf("chmod result-out: %v", err)
	}

	var out, errB bytes.Buffer
	rc := runWith(&fakeTr{rc: 0}, []string{"--result-format=json", "--result-out", outFile, "web01", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	data, err := os.ReadFile(outFile)
	if err != nil {
		t.Fatalf("read result-out: %v", err)
	}
	if string(data) != out.String() {
		t.Fatalf("result-out != stdout\nstdout: %s\nfile: %s", out.String(), data)
	}
	fi, err := os.Stat(outFile)
	if err != nil {
		t.Fatalf("stat result-out: %v", err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("mode=%o, want 600", fi.Mode().Perm())
	}
}

// Symlink result-out targets are refused rather than followed.
func TestRunResultFormatJSONResultOutSymlinkRefused(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	target := filepath.Join(t.TempDir(), "target.json")
	link := filepath.Join(t.TempDir(), "result.json")
	if err := os.WriteFile(target, []byte("untouched\n"), 0o600); err != nil {
		t.Fatalf("seed target: %v", err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatalf("symlink result-out: %v", err)
	}
	var out, errB bytes.Buffer
	rc := runWith(&fakeTr{rc: 0}, []string{"--result-format=json", "--result-out", link, "web01", "--", "true"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d", rc, exitUsage)
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read target: %v", err)
	}
	if string(data) != "untouched\n" {
		t.Fatalf("symlink target modified: %q", data)
	}
}

// --result-out on a directory refuses with exitUsage.
func TestRunResultFormatJSONResultOutDirRefused(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "web01")
	dir := t.TempDir()
	var out, errB bytes.Buffer
	rc := runWith(&fakeTr{}, []string{"--result-format=json", "--result-out", dir, "web01", "--", "true"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d", rc, exitUsage)
	}
}

func TestWriteResultOutWriteFailureDoesNotPublishPartialDocument(t *testing.T) {
	dir := t.TempDir()
	destination := filepath.Join(dir, "result.json")
	seedResultOutDestination(t, destination)
	wantErr := errors.New("injected write failure")
	createTemp := func(dir, pattern string) (resultOutTemp, error) {
		tmp, err := os.CreateTemp(dir, pattern)
		if err != nil {
			return nil, err
		}
		return failingResultOutTemp{resultOutTemp: tmp, writeErr: wantErr}, nil
	}

	var stderr bytes.Buffer
	if code := writeResultOutWith(createTemp, destination, []byte(`{"schema_version":"v1"}`), &stderr); code != exitUsage {
		t.Fatalf("code=%d, want %d", code, exitUsage)
	}
	if !strings.Contains(stderr.String(), wantErr.Error()) {
		t.Fatalf("stderr missing write failure: %q", stderr.String())
	}
	assertResultOutDestinationUnchanged(t, destination)
	assertNoResultOutTemps(t, dir)
}

func TestWriteResultOutCloseFailureDoesNotPublishPartialDocument(t *testing.T) {
	dir := t.TempDir()
	destination := filepath.Join(dir, "result.json")
	seedResultOutDestination(t, destination)
	wantErr := errors.New("injected close failure")
	createTemp := func(dir, pattern string) (resultOutTemp, error) {
		tmp, err := os.CreateTemp(dir, pattern)
		if err != nil {
			return nil, err
		}
		return failingResultOutTemp{resultOutTemp: tmp, closeErr: wantErr}, nil
	}

	var stderr bytes.Buffer
	if code := writeResultOutWith(createTemp, destination, []byte(`{"schema_version":"v1"}`), &stderr); code != exitUsage {
		t.Fatalf("code=%d, want %d", code, exitUsage)
	}
	if !strings.Contains(stderr.String(), wantErr.Error()) {
		t.Fatalf("stderr missing close failure: %q", stderr.String())
	}
	assertResultOutDestinationUnchanged(t, destination)
	assertNoResultOutTemps(t, dir)
}

func seedResultOutDestination(t *testing.T, destination string) {
	t.Helper()
	if err := os.WriteFile(destination, []byte("previous document\n"), 0o600); err != nil {
		t.Fatalf("seed result-out destination: %v", err)
	}
}

func assertResultOutDestinationUnchanged(t *testing.T, destination string) {
	t.Helper()
	data, err := os.ReadFile(destination)
	if err != nil {
		t.Fatalf("read result-out destination: %v", err)
	}
	if string(data) != "previous document\n" {
		t.Fatalf("destination changed after failed publication: %q", data)
	}
}

func assertNoResultOutTemps(t *testing.T, dir string) {
	t.Helper()
	temps, err := filepath.Glob(filepath.Join(dir, ".result.json.tmp-*"))
	if err != nil {
		t.Fatalf("glob result-out temporary files: %v", err)
	}
	if len(temps) != 0 {
		t.Fatalf("temporary result-out files remain: %v", temps)
	}
}
