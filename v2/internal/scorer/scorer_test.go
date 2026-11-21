package scorer

import (
	"testing"

	"github.com/ZJH233-tech/vpngate-clean-mihomo/v2/internal/store"
)

func TestScoreOneOrdering(t *testing.T) {
	kddi := ScoreOne(
		store.RawServer{IP: "106.173.94.75", CountryShort: "JP", Score: 500000, Speed: 400_000_000},
		"proto tcp\n<ca>x</ca>",
		&store.IPQuality{IP: "106.173.94.75", Country: "JP", ISP: "Kddi Corporation", Proxy: false, Hosting: false},
		store.NodeFeedback{},
	)
	official := ScoreOne(
		store.RawServer{IP: "219.100.37.113", CountryShort: "JP", Score: 900000, Speed: 700_000_000},
		"proto tcp\n<ca>x</ca>",
		&store.IPQuality{IP: "219.100.37.113", Country: "JP", ISP: "SoftEther", Proxy: true, Hosting: false},
		store.NodeFeedback{},
	)
	if kddi.Tier != 0 || kddi.Clean != 100 || !kddi.ResISP {
		t.Fatalf("expected KDDI tier=0 clean=100 resISP=true, got %+v", kddi)
	}
	if official.Tier != 2 || !official.Official {
		t.Fatalf("expected official cluster tier=2, got %+v", official)
	}
}
