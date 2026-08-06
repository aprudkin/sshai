// internal/shell/clixml_test.go
package shell

import (
	"reflect"
	"testing"
)

func TestFilterCLIXMLDropsWrapperKeepsPayload(t *testing.T) {
	in := "#< CLIXML\n" +
		"<Objs Version=\"1.1.0.1\"><S N=\"x\">noise</S></Objs>\n" +
		"real output line 1_x000D_\n" +
		"кириллица и emoji 🚀\n" +
		"<Obj RefId=\"0\">\n" +
		"real output line 2\n" +
		"\n\n"
	got := FilterCLIXML(in)
	want := []string{"real output line 1", "кириллица и emoji 🚀", "real output line 2"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestFilterCLIXMLEmptyInput(t *testing.T) {
	if got := FilterCLIXML(""); len(got) != 0 {
		t.Fatalf("want empty, got %q", got)
	}
}
