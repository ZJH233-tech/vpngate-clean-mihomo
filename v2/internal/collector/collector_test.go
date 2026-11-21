package collector

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestParseVPNGateCSVRejectsHTML(t *testing.T) {
	html := `<html><body><form><input name="__VIEWSTATE" value="abc"/></form></body></html>`
	if _, err := ParseVPNGateCSV(html); err == nil {
		t.Fatalf("expected HTML block page to be rejected")
	}
}

func TestParseVPNGateCSVAndWorkerJSON(t *testing.T) {
	ovpn := strings.Repeat("proto tcp\n<ca>\nCERT\n</ca>\n", 10)
	b64 := base64.StdEncoding.EncodeToString([]byte(ovpn))
	csv := "*vpn_servers\n#HostName,IP,...\n" +
		"vpn1,106.173.94.75,500000,15,450000000,Japan,JP,10,1000,100,1000,2weeks,op,msg,with,comma," + b64 + "\n"
	list, err := ParseVPNGateCSV(csv)
	if err != nil || len(list) != 1 || list[0].IP != "106.173.94.75" || list[0].CountryShort != "JP" {
		t.Fatalf("unexpected csv parse: len=%d err=%v", len(list), err)
	}

	j := `{"error":false,"servers":[{"hostName":"vpn1","ip":"106.173.94.75","score":500000,"ping":15,"speed":450000000,"countryShort":"jp","configDataBase64":"` + b64 + `","quality":{"risk":99}}]}`
	wlist, err := ParseWorkerJSON([]byte(j))
	if err != nil || len(wlist) != 1 || wlist[0].CountryShort != "JP" {
		t.Fatalf("unexpected worker json parse: len=%d err=%v", len(wlist), err)
	}
}
