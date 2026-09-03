// internal/shell/clixml.go
package shell

import "strings"

// Prefix list ported verbatim from ps_ssh.py (proven in production since 2026).
var clixmlPrefixes = []string{
	"#< CLIXML", "<Objs ", "</Objs>", "<Obj ", "</Obj>", "<TNRef", "<TS ",
	"<I64 ", "<U32 ", "<S N=", "<B N=", "<DT N=", "<I32 ", "<Nil ", "<Props",
	"<MS>", "<AV>", "<PI>", "<PC>", "<T>", "<SR>", "<SD>", "<AI>", "<LST>",
	"<G N=",
}

func FilterCLIXML(text string) []string {
	var kept []string
	for _, line := range strings.Split(text, "\n") {
		line = strings.ReplaceAll(line, "_x000D_", "")
		drop := false
		for _, p := range clixmlPrefixes {
			if strings.HasPrefix(line, p) {
				drop = true
				break
			}
		}
		if !drop {
			kept = append(kept, line)
		}
	}
	for len(kept) > 0 && strings.TrimSpace(kept[len(kept)-1]) == "" {
		kept = kept[:len(kept)-1]
	}
	return kept
}
