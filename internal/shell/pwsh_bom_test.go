package shell

import (
	"bytes"
	"testing"
)

// A body marker is not a command character inside the generated script block.
// Only its first UTF-8 BOM is removed; the wrapper still needs its own BOM.
func TestPwshBodyLeadingBOM(t *testing.T) {
	for _, tt := range []struct {
		name, body, want string
	}{
		{"command", "\ufeffWrite-Output 'Привет'", "Write-Output 'Привет'"},
		{"comment", "\ufeff# café\nWrite-Output 'ok'\n", "# café\nWrite-Output 'ok'\n"},
		{"param", "\ufeffparam($x = '日本語')\nWrite-Output $x", "param($x = '日本語')\nWrite-Output $x"},
		{"interior", "\ufeffWrite-Output 'a\ufeffb'", "Write-Output 'a\ufeffb'"},
		{"one marker only", "\ufeff\ufeffWrite-Output 'ok'", "\ufeffWrite-Output 'ok'"},
		{"not leading", " \ufeffWrite-Output 'ok'", " \ufeffWrite-Output 'ok'"},
		{"no marker", "Write-Output 'café'", "Write-Output 'café'"},
		{"empty", "", ""},
		{"marker only", "\ufeff", ""},
		{"no encoding guessing", "\xff\xfeX\x00", "\xff\xfeX\x00"},
	} {
		for _, marker := range []string{"", "START"} {
			t.Run(tt.name+"/"+marker, func(t *testing.T) {
				var script []byte
				prefix, suffix := "  . {\n", "  }\n"
				if marker == "" {
					script = PwshScript(tt.body, State{}, nil, "END")
				} else {
					script = PwshScriptFollow(tt.body, State{}, nil, "END", marker)
					prefix, suffix = "  & {\n", "  } *>&1\n"
				}
				if !bytes.HasPrefix(script, []byte("\ufeff[Console]")) {
					t.Fatal("wrapper lost its outer encoding marker")
				}
				want := []byte(tt.want)
				if !bytes.HasSuffix(want, []byte("\n")) {
					want = append(want, '\n')
				}
				if !bytes.Contains(script, []byte(prefix+string(want)+suffix)) {
					t.Fatalf("wrapper did not embed the expected body %q", tt.want)
				}
			})
		}
	}
}
