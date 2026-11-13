package main

import "testing"

func TestExtractDirectPathFromURL(t *testing.T) {
	casos := []struct{ nome, url, esperado string }{
		{
			"mantém a query e tira só o mms3",
			"https://mmg.whatsapp.net/v/t62.7118-24/123_n.enc?ccb=11-4&oh=01_x&oe=6B1E0209&_nc_sid=5e03e0&mms3=true",
			"/v/t62.7118-24/123_n.enc?ccb=11-4&oh=01_x&oe=6B1E0209&_nc_sid=5e03e0",
		},
		{
			"sem mms3 fica igual",
			"https://mmg.whatsapp.net/o1/v/t24/f2/m233/AQOR?ccb=9-4&oh=01_y&oe=6B1E0209",
			"/o1/v/t24/f2/m233/AQOR?ccb=9-4&oh=01_y&oe=6B1E0209",
		},
		{"sem domínio reconhecível", "/ja/e/um/caminho?a=1", "/ja/e/um/caminho?a=1"},
	}
	for _, c := range casos {
		if got := extractDirectPathFromURL(c.url); got != c.esperado {
			t.Errorf("%s: obtido %q, esperado %q", c.nome, got, c.esperado)
		}
	}
}
