package netutil

import "testing"

func TestExtractASN(t *testing.T) {
	if got := ExtractASN("AS132203 Tencent Building, Kejizhongyi Avenue"); got != "AS132203" {
		t.Fatalf("got %q, want AS132203", got)
	}
	if got := ExtractASN("no asn here"); got != "" {
		t.Fatalf("got %q, want empty", got)
	}
}

func TestIsLeakOrInvalid(t *testing.T) {
	main := MainEgress{IP: "43.133.157.63", Country: "ID", ASN: "AS132203"}

	// 正常日本住宅出口
	if bad, reason := IsLeakOrInvalid("106.173.94.75", "JP", "AS2516", "JP", main); bad {
		t.Fatalf("expected valid JP egress, got bad=%v reason=%s", bad, reason)
	}
	// 主 NAT 换了网段但仍命中主 ASN（43.173.9.190 -> AS132203）
	if bad, _ := IsLeakOrInvalid("43.173.9.190", "JP", "AS132203", "JP", main); !bad {
		t.Fatalf("expected leak detection on matching main ASN")
	}
	// 出口国家为 ID（雅加达 NAT）或链式出口为 US
	if bad, _ := IsLeakOrInvalid("43.157.206.90", "ID", "AS132203", "JP", main); !bad {
		t.Fatalf("expected invalid on non-JP country")
	}
	if bad, _ := IsLeakOrInvalid("1.2.3.4", "US", "AS7922", "JP", main); !bad {
		t.Fatalf("expected invalid on US chain relay")
	}
}
