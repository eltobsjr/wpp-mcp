package main

// Ações sobre mensagens já existentes: citar, reagir, editar, apagar e marcar como lido.
// Ficam fora do main.go para manter o bridge original intacto e facilitar a revisão.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// ActionRequest é o corpo comum dos endpoints de ação.
type ActionRequest struct {
	Chat       string   `json:"chat"`
	MessageID  string   `json:"message_id,omitempty"`
	MessageIDs []string `json:"message_ids,omitempty"`
	Text       string   `json:"text,omitempty"`
	Emoji      string   `json:"emoji,omitempty"`
}

// ActionResponse é a resposta comum.
type ActionResponse struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
	ID      string `json:"id,omitempty"`
}

// parseChat aceita JID completo ou só telefone.
func parseChat(chat string) (types.JID, error) {
	chat = strings.TrimSpace(chat)
	if chat == "" {
		return types.JID{}, fmt.Errorf("chat é obrigatório")
	}
	if strings.Contains(chat, "@") {
		return types.ParseJID(chat)
	}
	return types.JID{User: chat, Server: types.DefaultUserServer}, nil
}

// storedSenderJID converte o remetente guardado no banco (só o "user") em JID.
// Em conversa 1:1 o remetente é o próprio chat; em grupo adivinhamos o servidor pelo tamanho
// do identificador (LID tem 15+ dígitos).
func storedSenderJID(client *whatsmeow.Client, chat types.JID, sender string, fromMe bool) types.JID {
	if fromMe {
		if client.Store.ID != nil {
			return client.Store.ID.ToNonAD()
		}
		return types.EmptyJID
	}
	if strings.Contains(sender, "@") {
		if j, err := types.ParseJID(sender); err == nil {
			return j
		}
	}
	if chat.Server != types.GroupServer {
		return chat
	}
	if len(sender) >= 15 {
		return types.JID{User: sender, Server: types.HiddenUserServer}
	}
	return types.JID{User: sender, Server: types.DefaultUserServer}
}

// lookupMessage lê remetente, texto e autoria de uma mensagem guardada.
func lookupMessage(store *MessageStore, chat types.JID, id string) (sender, content string, fromMe bool, ok bool) {
	row := store.db.QueryRow(
		"SELECT sender, content, is_from_me FROM messages WHERE id = ? AND chat_jid = ?", id, chat.String())
	if err := row.Scan(&sender, &content, &fromMe); err != nil {
		return "", "", false, false
	}
	return sender, content, fromMe, true
}

// quoteContext monta o ContextInfo de uma resposta citada. Erro se a mensagem não está no histórico.
func quoteContext(client *whatsmeow.Client, store *MessageStore, chat types.JID, replyTo string) (*waProto.ContextInfo, error) {
	sender, content, fromMe, ok := lookupMessage(store, chat, replyTo)
	if !ok {
		return nil, fmt.Errorf("mensagem %s não encontrada em %s (só dá para citar o que está no histórico)", replyTo, chat)
	}
	part := storedSenderJID(client, chat, sender, fromMe)
	ci := &waProto.ContextInfo{
		StanzaID:      proto.String(replyTo),
		QuotedMessage: &waProto.Message{Conversation: proto.String(content)},
	}
	if !part.IsEmpty() {
		ci.Participant = proto.String(part.String())
	}
	return ci, nil
}

// applyQuote coloca o ContextInfo na parte da mensagem que existir (texto ou mídia).
func applyQuote(msg *waProto.Message, ci *waProto.ContextInfo) {
	switch {
	case msg.ExtendedTextMessage != nil:
		msg.ExtendedTextMessage.ContextInfo = ci
	case msg.ImageMessage != nil:
		msg.ImageMessage.ContextInfo = ci
	case msg.VideoMessage != nil:
		msg.VideoMessage.ContextInfo = ci
	case msg.AudioMessage != nil:
		msg.AudioMessage.ContextInfo = ci
	case msg.DocumentMessage != nil:
		msg.DocumentMessage.ContextInfo = ci
	}
}

func writeAction(w http.ResponseWriter, ok bool, message, id string) {
	w.Header().Set("Content-Type", "application/json")
	if !ok {
		w.WriteHeader(http.StatusInternalServerError)
	}
	json.NewEncoder(w).Encode(ActionResponse{Success: ok, Message: message, ID: id})
}

// registerActionRoutes liga /api/react, /api/edit, /api/revoke e /api/read.
func registerActionRoutes(mux *http.ServeMux, client *whatsmeow.Client, store *MessageStore) {
	handler := func(run func(req ActionRequest, chat types.JID) (bool, string, string)) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
				return
			}
			var req ActionRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				http.Error(w, "Invalid request format", http.StatusBadRequest)
				return
			}
			if !client.IsConnected() {
				writeAction(w, false, "Not connected to WhatsApp", "")
				return
			}
			chat, err := parseChat(req.Chat)
			if err != nil {
				writeAction(w, false, err.Error(), "")
				return
			}
			ok, msg, id := run(req, chat)
			writeAction(w, ok, msg, id)
		}
	}

	mux.HandleFunc("/api/react", handler(func(req ActionRequest, chat types.JID) (bool, string, string) {
		if req.MessageID == "" {
			return false, "message_id é obrigatório", ""
		}
		sender, _, fromMe, ok := lookupMessage(store, chat, req.MessageID)
		if !ok {
			return false, "mensagem não encontrada no histórico", ""
		}
		target := storedSenderJID(client, chat, sender, fromMe)
		resp, err := client.SendMessage(context.Background(), chat, client.BuildReaction(chat, target, req.MessageID, req.Emoji))
		if err != nil {
			return false, fmt.Sprintf("Error sending reaction: %v", err), ""
		}
		return true, "Reaction sent", resp.ID
	}))

	mux.HandleFunc("/api/edit", handler(func(req ActionRequest, chat types.JID) (bool, string, string) {
		if req.MessageID == "" || strings.TrimSpace(req.Text) == "" {
			return false, "message_id e text são obrigatórios", ""
		}
		_, _, fromMe, ok := lookupMessage(store, chat, req.MessageID)
		if ok && !fromMe {
			return false, "só dá para editar mensagem sua", ""
		}
		edit := client.BuildEdit(chat, req.MessageID, &waProto.Message{Conversation: proto.String(req.Text)})
		resp, err := client.SendMessage(context.Background(), chat, edit)
		if err != nil {
			return false, fmt.Sprintf("Error editing message: %v", err), ""
		}
		return true, "Message edited", resp.ID
	}))

	mux.HandleFunc("/api/revoke", handler(func(req ActionRequest, chat types.JID) (bool, string, string) {
		if req.MessageID == "" {
			return false, "message_id é obrigatório", ""
		}
		_, _, fromMe, ok := lookupMessage(store, chat, req.MessageID)
		if ok && !fromMe {
			return false, "só dá para apagar mensagem sua", ""
		}
		resp, err := client.SendMessage(context.Background(), chat, client.BuildRevoke(chat, types.EmptyJID, req.MessageID))
		if err != nil {
			return false, fmt.Sprintf("Error revoking message: %v", err), ""
		}
		return true, "Message revoked", resp.ID
	}))

	mux.HandleFunc("/api/read", handler(func(req ActionRequest, chat types.JID) (bool, string, string) {
		type item struct{ id, sender string }
		var items []item
		if len(req.MessageIDs) > 0 {
			for _, id := range req.MessageIDs {
				s, _, fromMe, ok := lookupMessage(store, chat, id)
				if ok && !fromMe {
					items = append(items, item{id, s})
				}
			}
		} else {
			rows, err := store.db.Query(
				"SELECT id, sender FROM messages WHERE chat_jid = ? AND is_from_me = 0 ORDER BY timestamp DESC LIMIT 20", chat.String())
			if err != nil {
				return false, fmt.Sprintf("Error querying messages: %v", err), ""
			}
			defer rows.Close()
			for rows.Next() {
				var it item
				if rows.Scan(&it.id, &it.sender) == nil {
					items = append(items, it)
				}
			}
		}
		if len(items) == 0 {
			return false, "nenhuma mensagem recebida para marcar como lida", ""
		}
		bySender := map[string][]types.MessageID{}
		for _, it := range items {
			bySender[it.sender] = append(bySender[it.sender], it.id)
		}
		for sender, ids := range bySender {
			err := client.MarkRead(context.Background(), ids, time.Now(), chat, storedSenderJID(client, chat, sender, false))
			if err != nil {
				return false, fmt.Sprintf("Error marking as read: %v", err), ""
			}
		}
		return true, fmt.Sprintf("%d mensagem(ns) marcada(s) como lida(s)", len(items)), ""
	}))
}

// rememberSent guarda no histórico o que foi enviado pela API, para poder citar, reagir e listar depois.
// Falha aqui nunca derruba o envio (a mensagem já saiu).
func rememberSent(client *whatsmeow.Client, store *MessageStore, chat types.JID, id, text, mediaPath string, msg *waProto.Message) {
	if store == nil || id == "" {
		return
	}
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(msg)
	if mediaType != "" && mediaPath != "" {
		filename = mediaPath[strings.LastIndex(mediaPath, "/")+1:]
	}
	sender := ""
	if client.Store.ID != nil {
		sender = client.Store.ID.User
	}
	_ = store.StoreMessage(id, chat.String(), sender, text, time.Now(), true, mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength)
}
