package shell

import "testing"

// Descendants can write after the direct shell's state epilogue. Stripping
// the epilogue must not discard those bytes (including absent final newlines).
func TestParsersPreservePostEpilogueOutput(t *testing.T) {
	for _, tc := range []struct {
		name  string
		parse func([]byte, string) ([]byte, State, bool)
		env   string
	}{
		{"bash", BashParse, "QT1vbmUA"}, // A=one + NUL
		{"pwsh", PwshParse, "QT1vbmU="},
	} {
		for _, suffix := range []string{"after\n", "after", "\nafter\n", "\n\n", " \n", ""} {
			t.Run(tc.name+"/"+suffix, func(t *testing.T) {
				raw := []byte("before\n\nS\n/work\n" + tc.env + "\n" + suffix)
				out, state, ok := tc.parse(raw, "S")
				if !ok || string(out) != "before\n"+suffix || state.Cwd != "/work" || state.Env["A"] != "one" {
					t.Fatalf("out=%q state=%+v ok=%v", out, state, ok)
				}
			})
		}
	}
}
