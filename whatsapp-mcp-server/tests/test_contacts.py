import multiprocessing

import pytest

import contacts
from contacts import RecipientError


def test_resolve_nome_unico(agenda):
    agenda([{"name": "João Silva", "phone": "5511999998888", "notes": ""}])
    assert contacts.resolve("joao silva") == "5511999998888"


def test_resolve_passa_jid_e_normaliza_telefone(agenda):
    agenda([])
    assert contacts.resolve("123@g.us") == "123@g.us"
    assert contacts.resolve("+55 (11) 99999-8888") == "5511999998888"


def test_resolve_nome_ambiguo_levanta_com_candidatos(agenda):
    agenda([
        {"name": "Maria", "phone": "5511111111111", "notes": ""},
        {"name": "maria", "phone": "5522222222222", "notes": ""},
    ])
    with pytest.raises(RecipientError) as exc:
        contacts.resolve("Maria")
    assert len(exc.value.candidates) == 2


def test_resolve_nome_inexistente_nao_vira_numero(agenda):
    agenda([{"name": "Maria Souza", "phone": "5511111111111", "notes": ""}])
    with pytest.raises(RecipientError) as exc:
        contacts.resolve("Maria")
    assert exc.value.candidates[0]["name"] == "Maria Souza"


def test_resolve_vazio(agenda):
    agenda([])
    with pytest.raises(RecipientError):
        contacts.resolve("  ")


def test_save_contact_atualiza_pelo_telefone(agenda):
    agenda([])
    contacts.save_contact("Ana", "11999998888")
    contacts.save_contact("Ana Paula", "5511999998888", "nota")
    items = contacts.list_contacts()
    assert len(items) == 1
    assert items[0]["name"] == "Ana Paula" and items[0]["notes"] == "nota"


def _gravar(path, i):
    contacts.CONTACTS_PATH = path
    contacts.save_contact(f"Pessoa {i}", f"55119{i:08d}")


def test_escritas_concorrentes_nao_perdem_contatos(agenda, tmp_path):
    agenda([])
    path = str(tmp_path / "contacts.json")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_gravar, args=(path, i)) for i in range(12)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert len(contacts.list_contacts()) == 12


def test_import_vcf_nao_sobrescreve_existente(agenda, tmp_path):
    agenda([{"name": "Editado", "phone": "5511999998888", "notes": ""}])
    vcf = tmp_path / "c.vcf"
    vcf.write_text("BEGIN:VCARD\nFN:Original\nTEL:11999998888\nEND:VCARD\n"
                   "BEGIN:VCARD\nFN:Novo\nTEL:11988887777\nEND:VCARD\n", encoding="utf-8")
    result = contacts.import_vcf(str(vcf))
    assert result == {"found": 2, "added": 1, "already_present": 1}
    assert {c["name"] for c in contacts.list_contacts()} == {"Editado", "Novo"}


def test_set_profile_parcial_e_limpeza(agenda):
    agenda([{"name": "Ana", "phone": "5511999998888", "notes": ""}])
    contacts.set_profile("ana", tone="informal", address_as="Aninha")
    contacts.set_profile("5511999998888", signature="abç", tone=None)
    assert contacts.get_profile("Ana") == {"tone": "informal", "address_as": "Aninha", "signature": "abç"}
    contacts.set_profile("Ana", tone="", address_as="", signature="")
    assert contacts.get_profile("Ana") == {}
    assert "profile" not in contacts.list_contacts()[0]


def test_set_profile_valida_campos(agenda):
    agenda([{"name": "Ana", "phone": "5511999998888", "notes": ""}])
    with pytest.raises(ValueError):
        contacts.set_profile("Ana", reply_mode="sempre")
    with pytest.raises(ValueError):
        contacts.set_profile("Ana", humor="alto")
    contacts.set_profile("Ana", reply_mode="draft")
    assert contacts.get_profile("Ana")["reply_mode"] == "draft"


def test_set_profile_contato_inexistente(agenda):
    agenda([])
    with pytest.raises(RecipientError):
        contacts.set_profile("Fulano", tone="x")
    assert contacts.get_profile("Fulano") is None


def test_perfil_sobrevive_a_save_contact(agenda):
    agenda([{"name": "Ana", "phone": "5511999998888", "notes": ""}])
    contacts.set_profile("Ana", tone="informal")
    contacts.save_contact("Ana Maria", "5511999998888")
    assert contacts.get_profile("Ana Maria") == {"tone": "informal"}


def test_find_contact_por_telefone_com_e_sem_nono_digito(agenda):
    agenda([{"name": "Ana", "phone": "558994127883", "notes": ""}])
    assert contacts.find_contact("5589994127883")["name"] == "Ana"
