package main

import (
	"testing"

	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

func TestParseChat(t *testing.T) {
	j, err := parseChat("5511999990001")
	if err != nil || j.Server != types.DefaultUserServer || j.User != "5511999990001" {
		t.Fatalf("telefone: %v %v", j, err)
	}
	g, err := parseChat("120363385152659217@g.us")
	if err != nil || g.Server != types.GroupServer {
		t.Fatalf("grupo: %v %v", g, err)
	}
	if _, err := parseChat("  "); err == nil {
		t.Fatal("chat vazio deveria falhar")
	}
}

func TestApplyQuoteEmTextoEMidia(t *testing.T) {
	ci := &waProto.ContextInfo{StanzaID: proto.String("ABC")}
	txt := &waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("oi")}}
	applyQuote(txt, ci)
	if txt.ExtendedTextMessage.ContextInfo.GetStanzaID() != "ABC" {
		t.Fatal("texto sem citação")
	}
	doc := &waProto.Message{DocumentMessage: &waProto.DocumentMessage{}}
	applyQuote(doc, ci)
	if doc.DocumentMessage.ContextInfo.GetStanzaID() != "ABC" {
		t.Fatal("documento sem citação")
	}
}
