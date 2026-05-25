from __future__ import annotations

import csv, os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional, Callable
import itertools
from datetime import datetime
import tkinter.font as tkfont
from math import isnan
import webbrowser
from tkinter import filedialog
from urllib.parse import quote
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
import tempfile, subprocess, shutil
import re


# Funções de Formatação #

def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())

def format_telefone(s: str) -> str:
    d = _digits_only(s)[:11]
    if len(d) <= 2:
        return d
    if len(d) <= 7:
        return f"({d[:2]}) {d[2:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return f"({d[:2]}) {d[2:7]}-{d[7:]}"

def format_cpf(s: str) -> str:
    d = _digits_only(s)[:11]
    if len(d) <= 3:
        return d
    if len(d) <= 6:
        return f"{d[:3]}.{d[3:]}"
    if len(d) <= 9:
        return f"{d[:3]}.{d[3:6]}.{d[6:]}"
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"

def format_cnpj(s: str) -> str:
    d = _digits_only(s)[:14]
    if len(d) <= 2:
        return d
    if len(d) <= 5:
        return f"{d[:2]}.{d[2:]}"
    if len(d) <= 8:
        return f"{d[:2]}.{d[2:5]}.{d[5:]}"
    if len(d) <= 12:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:]}"
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"

def format_date(s: str) -> str:
    d = "".join(ch for ch in s if ch.isdigit())[:8]
    if len(d) <= 2:  return d
    if len(d) <= 4:  return f"{d[:2]}/{d[2:]}"
    return f"{d[:2]}/{d[2:4]}/{d[4:]}"

def format_percent(s: str) -> str:
    d = "".join(ch for ch in s if ch.isdigit())[:3]  
    return f"{d}%" if d else ""

def format_money(s: str) -> str:
    digits = "".join(ch for ch in s if ch.isdigit())

    if digits == "":
        return ""

    value = int(digits)

    reais = value // 100
    cents = value % 100

    formatted = f"R$ {reais:,},{cents:02d}"

    formatted = formatted.replace(",", "X", 1).replace(".", ",").replace("X", ".")

    return formatted

def format_cep(s: str) -> str:
            d = "".join(ch for ch in s if ch.isdigit())[:8]
            if len(d) > 5:
                return f"{d[:5]}-{d[5:]}"
            return d

def parse_money(s: str) -> float:
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 0.0

    # interpreta sempre como centavos
    centavos = int(digits)
    return centavos / 100.0

def money_str(valor: float) -> str:
    try:
        v = float(valor or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    centavos = int(round(v * 100))
    if centavos <= 0:
        return "R$ 0,00"
    return format_money(str(centavos))

def parse_percent_str(p: str) -> float:
    """
    "10%" -> 0.10
    """
    p = (p or "").strip()
    if not p:
        return 0.0
    # pega só dígitos
    digits = "".join(ch for ch in p if ch.isdigit())
    return (float(digits) / 100.0) if digits else 0.0


class MaskedEntry(ttk.Entry):
    def __init__(self, master, format_fn, textvariable: tk.StringVar | None = None, **kw):
        self._format_fn = format_fn
        self._var = textvariable or tk.StringVar()
        super().__init__(master, textvariable=self._var, **kw)

        self.bind("<KeyRelease>", self._apply_mask)
        self.bind("<<Paste>>", self._apply_mask)

    def _apply_mask(self, _evt=None):
        cur = self._var.get()
        formatted = self._format_fn(cur)
        if formatted != cur:
            self._var.set(formatted)
            self.icursor(len(formatted))

    def get(self) -> str:
        return self._var.get()


# -----------------------------
# Outras funções auxiliares
# -----------------------------

def get_data_dir(appname="ImobManager") -> Path:
    base = Path(os.environ.get("APPDATA", Path.home()))
    data_dir = base / appname / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def calcular_resumos_proprietario_periodo(store: "DataStore", proprietario_id: int, mes: int, ano: int) -> list[dict]:
    imoveis = [i for i in store.list_imoveis() if getattr(i, "proprietario_id", None) == proprietario_id]

    resumos = []
    for imv in imoveis:
        resumo = store.calcular_resumo_imovel_periodo(imv.id, mes, ano)

        # garante período no resumo (pra PDF/WhatsApp/etc)
        resumo["mes"] = mes
        resumo["ano"] = ano

        resumos.append(resumo)

    return resumos

def calcular_honorario_imovel_periodo(imv, resumo_locador: dict) -> float:
    """
    Tipo A: % do aluguel bruto
    Tipo C: % da soma dos créditos das ocorrências do locador no mês
    """
    taxa = parse_percent_str(getattr(imv, "honorarios_percentual", ""))
    tipo = (getattr(imv, "honorarios_tipo", "A") or "A").strip().upper()

    aluguel = float(resumo_locador.get("valor_aluguel", 0.0) or 0.0)
    creditos_locador = float(resumo_locador.get("total_creditos", 0.0) or 0.0)

    if tipo == "C":
        return creditos_locador * taxa

    # default: A
    return aluguel * taxa

def gerar_pdf_recibo_imovel(path: str, resumo: dict):
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4

    imv = resumo["imovel"]
    ocorrencias = resumo["ocorrencias"]
    total_debitos = resumo["total_debitos"]
    total_creditos = resumo["total_creditos"]
    total_pagar = resumo["total_a_pagar"]
    valor_aluguel = resumo["valor_aluguel"]

    locatario_nome = (resumo.get("locatario_nome") or "-").strip()
    proprietario_nome = (resumo.get("proprietario_nome") or "-").strip()

    obs = (resumo.get("observacao_boleta") or "").strip()
    venc = (getattr(imv, "dia_vencimento", "") or "").strip()

    left = 20 * mm
    top = h - 18 * mm
    y = top

    # ---------- CABEÇALHO ----------
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "ESCRITÓRIO DE ADVOCACIA")
    y -= 4 * mm

    c.setFont("Helvetica", 9)
    c.drawString(left, y, "Rua Carolina Machado 542 Sala 213 - Madureira")
    y -= 4 * mm

    c.drawString(left, y, "Tel: (21) 3390-8033 | (21) 98706-6774")
    y -= 4 * mm

    c.line(left, y, left + 170 * mm, y)
    y -= 8 * mm

    # ---------- TÍTULO ----------
    c.setFont("Helvetica-Bold", 15)
    c.drawString(left, y, "BOLETA DE ALUGUEL")
    y -= 8 * mm

    # ---------- PERÍODO + VENCIMENTO ----------
    mes = int(resumo.get("mes", 0) or 0)
    ano = int(resumo.get("ano", 0) or 0)

    c.setFont("Helvetica", 10)
    periodo_txt = f"{mes:02d}/{ano}" if mes and ano else "--/----"
    c.drawString(left, y, f"Período: {periodo_txt}")
    if venc:
        c.drawString(left + 90*mm, y, f"Vencimento: dia {venc}")
    else:
        c.drawString(left + 90*mm, y, "Vencimento: -")
    y -= 8 * mm

    # ---------- DADOS ----------
    c.setFont("Helvetica", 10)
    c.drawString(left, y, f"Locatário: {locatario_nome}")
    y -= 5 * mm
    c.drawString(left, y, f"Proprietário: {proprietario_nome}")
    y -= 5 * mm
    c.drawString(left, y, f"Endereço: {imv.endereco or '-'}")
    y -= 5 * mm
    c.drawString(left, y, f"Aluguel: {money_str(valor_aluguel)}")
    y -= 8 * mm

    # ---------- OCORRÊNCIAS ----------
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Ocorrências:")
    y -= 7 * mm

    c.setFont("Helvetica-Bold", 10)
    x_data = left
    x_desc = left + 35 * mm
    x_tipo = left + 125 * mm
    x_valor = left + 170 * mm

    c.drawString(x_data, y, "Data")
    c.drawString(x_desc, y, "Descrição")
    c.drawString(x_tipo, y, "Tipo")
    c.drawRightString(x_valor, y, "Valor")
    y -= 2 * mm
    c.line(left, y, left + 170 * mm, y)
    y -= 6 * mm

    c.setFont("Helvetica", 10)

    if not ocorrencias:
        c.drawString(left, y, "Não há ocorrências no período.")
        y -= 6 * mm
    else:
        for oc in ocorrencias:
            desc = (oc.descricao or "")
            if len(desc) > 55:
                desc = desc[:55] + "..."

            # quebra de página se estiver muito embaixo
            if y < 45 * mm:
                c.showPage()
                y = top

            c.drawString(x_data, y, str(oc.data))
            c.drawString(x_desc, y, desc)
            c.drawString(x_tipo, y, str(oc.tipo))
            c.drawRightString(x_valor, y, money_str(float(oc.valor)))
            y -= 5 * mm

    y -= 2 * mm
    c.line(left, y, left + 170 * mm, y)
    y -= 10 * mm

    # ---------- TOTAIS ----------
    c.setFont("Helvetica", 10)
    c.drawString(left, y, f"Total de débitos: {money_str(total_debitos)}")
    y -= 6 * mm
    c.drawString(left, y, f"Total de créditos: {money_str(total_creditos)}")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(left + 170 * mm, y, f"TOTAL A PAGAR: {money_str(total_pagar)}")
    y -= 14 * mm

    # ---------- RECEBIDO EM + ASSINATURA ----------
    c.setFont("Helvetica", 10)

    # texto da data
    c.drawString(left, y, "Recebido em: ____/____/______")

    # linha de assinatura ao lado da data
    linha_x_inicio = left + 95 * mm
    linha_x_fim    = left + 170 * mm

    c.setLineWidth(1)
    c.line(linha_x_inicio, y - 2 * mm, linha_x_fim, y - 2 * mm)

    y -= 18 * mm

    # ---------- OBSERVAÇÃO ----------
    if obs:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, "Observação:")
        y -= 6 * mm
        c.setFont("Helvetica", 10)
        c.drawString(left, y, obs)
        y -= 10 * mm

    # ---------- RODAPÉ FIXO ----------
    c.setFont("Helvetica", 9)
    c.drawString(left, 18 * mm, "Este recibo não quita débitos anteriores.")

    c.showPage()
    c.save()

def gerar_pdf_financeiro_mes(path: str, store: "DataStore", mes: int, ano: int):
    if pdf_canvas is None:
        raise RuntimeError(
            "Biblioteca 'reportlab' não está instalada.\n"
            "Instale com: pip install reportlab"
        )

    c = pdf_canvas.Canvas(path, pagesize=A4)
    largura, altura = A4

    margem_x = 18 * mm
    y = altura - 18 * mm

    def cabecalho():
        nonlocal y
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margem_x, y, f"FINANCEIRO DO MÊS - {mes:02d}/{ano}")
        y -= 10 * mm

        c.setFont("Helvetica-Bold", 10)
        c.drawString(margem_x, y, "Locatário")
        c.drawString(margem_x + 80*mm, y, "Imóvel")
        c.drawRightString(largura - margem_x, y, "Total (R$)")
        y -= 3 * mm
        c.line(margem_x, y, largura - margem_x, y)
        y -= 5 * mm

    cabecalho()

    total_geral = 0.0
    total_honorarios_geral = 0.0

    # Ordena por nome do locatário (quando existir) pra ficar “conferível”
    imoveis = store.list_imoveis()

    def nome_locatario(imv):
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            return (loc.nome or "").strip().lower() if loc else ""
        return ""

    imoveis.sort(key=lambda i: (nome_locatario(i), (i.descricao or "").lower()))

    c.setFont("Helvetica", 10)

    for imv in imoveis:
        resumo = store.calcular_resumo_imovel_periodo(imv.id, mes, ano)

        # nome completo do locatário
        loc_nome = "-"
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            if loc and getattr(loc, "nome", ""):
                loc_nome = loc.nome.strip()

        imv_nome = (imv.descricao or "").strip() or f"ID {imv.id}"
        total = float(resumo.get("total_a_pagar", 0.0) or 0.0)
        
        # honorários (usa o resumo do LOCADOR)
        resumo_locador = store.calcular_resumo_imovel_periodo(imv.id, mes, ano, parte="locador")
        honorarios = calcular_honorario_imovel_periodo(imv, resumo_locador)
        total_honorarios_geral += float(honorarios or 0.0)

        # quebra de página
        if y < 20 * mm:
            c.showPage()
            y = altura - 18 * mm
            cabecalho()
            c.setFont("Helvetica", 10)

        # corta texto pra não invadir colunas
        if len(loc_nome) > 35:
            loc_nome = loc_nome[:35] + "..."
        if len(imv_nome) > 40:
            imv_nome = imv_nome[:40] + "..."

        c.drawString(margem_x, y, loc_nome)
        c.drawString(margem_x + 80*mm, y, imv_nome)
        c.drawRightString(largura - margem_x, y, money_str(total))

        total_geral += total
        y -= 5 * mm

    # total final
    y -= 4 * mm
    c.line(margem_x, y, largura - margem_x, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(largura - margem_x, y, f"TOTAL GERAL: {money_str(total_geral)}")
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(largura - margem_x, y, f"TOTAL DE HONORÁRIOS (GERAL): {money_str(total_honorarios_geral)}")

    c.showPage()
    c.save()

def gerar_pdf_balancete_proprietario(path: str, store: "DataStore", proprietario_id: int, mes: int, ano: int):
    if pdf_canvas is None:
        raise RuntimeError("Biblioteca 'reportlab' não está instalada. Instale com: pip install reportlab")

    owner = store.get_locador(proprietario_id)
    if not owner:
        raise ValueError("Proprietário não encontrado.")

    imoveis = [i for i in store.list_imoveis() if i.proprietario_id == proprietario_id]
    imoveis.sort(key=lambda x: (x.descricao or "").lower())

    c = pdf_canvas.Canvas(path, pagesize=A4)
    largura, altura = A4
    margem_x = 16 * mm
    y = altura - 16 * mm

    total_liquido_geral = 0.0
    total_honorarios_geral = 0.0

    def cabecalho():
        nonlocal y
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margem_x, y, "CONTROLE ADMINISTRATIVO DE IMÓVEIS - BALANCETE")
        y -= 6 * mm
        c.setFont("Helvetica", 10)
        c.drawString(margem_x, y, f"Período: {mes:02d}/{ano}")
        y -= 5 * mm
        c.drawString(margem_x, y, f"Proprietário: {owner.nome} (ID {owner.id})")
        y -= 7 * mm
        c.line(margem_x, y, largura - margem_x, y)
        y -= 6 * mm

    def titulo_imovel(imv):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margem_x, y, f"Imóvel: {imv.descricao or f'ID {imv.id}'}   |   ID: {imv.id}")
        y -= 5 * mm
        c.setFont("Helvetica", 9)
        if imv.endereco:
            c.drawString(margem_x, y, f"Endereço: {imv.endereco}")
            y -= 5 * mm

    def cabecalho_tabela():
        nonlocal y
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margem_x, y, "Descrição")
        c.drawRightString(largura - margem_x - 40*mm, y, "Crédito (R$)")
        c.drawRightString(largura - margem_x, y, "Débito (R$)")
        y -= 3 * mm
        c.line(margem_x, y, largura - margem_x, y)
        y -= 5 * mm
        c.setFont("Helvetica", 9)

    def quebra_pagina_se_preciso(min_y=30*mm):
        nonlocal y
        if y < min_y:
            c.showPage()
            y = altura - 16 * mm
            cabecalho()

    cabecalho()

    for imv in imoveis:
        # resumo do locador (ocorrências do proprietário)
        resumo = store.calcular_resumo_imovel_periodo(imv.id, mes, ano, parte="locador")

        # créditos e débitos do locador (ocorrências)
        total_debitos = float(resumo.get("total_debitos", 0.0) or 0.0)
        total_creditos_oc = float(resumo.get("total_creditos", 0.0) or 0.0)

        aluguel = float(resumo.get("valor_aluguel", 0.0) or 0.0)

        # no balancete, aluguel entra como CRÉDITO
        creditos_total = aluguel + total_creditos_oc
        debitos_total = total_debitos

        honorarios = calcular_honorario_imovel_periodo(imv, resumo)
        liquido = (creditos_total - debitos_total) - honorarios

        total_honorarios_geral += honorarios
        total_liquido_geral += liquido

        quebra_pagina_se_preciso(55*mm)
        titulo_imovel(imv)
        cabecalho_tabela()

        # Linha do aluguel como crédito
        c.drawString(margem_x, y, "Aluguel mensal")
        c.drawRightString(largura - margem_x - 40*mm, y, money_str(aluguel))
        c.drawRightString(largura - margem_x, y, money_str(0.0))
        y -= 5 * mm

        # Ocorrências do locador
        ocorrs = resumo.get("ocorrencias", []) or []
        ocorrs.sort(key=lambda oc: (oc.data or "", oc.descricao or ""))

        for oc in ocorrs:
            quebra_pagina_se_preciso(35*mm)

            desc = (oc.descricao or "").strip() or "Ocorrência"
            if len(desc) > 55:
                desc = desc[:55] + "..."

            tipo = (oc.tipo or "").lower()
            cred = oc.valor if tipo.startswith("c") else 0.0
            deb = oc.valor if tipo.startswith("d") else 0.0

            c.drawString(margem_x, y, f"{oc.data}  {desc}")
            c.drawRightString(largura - margem_x - 40*mm, y, money_str(cred))
            c.drawRightString(largura - margem_x, y, money_str(deb))
            y -= 5 * mm

        # Totais do imóvel
        y -= 2 * mm
        c.line(margem_x, y, largura - margem_x, y)
        y -= 6 * mm
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margem_x, y, "Totais")
        c.drawRightString(largura - margem_x - 40*mm, y, money_str(creditos_total))
        c.drawRightString(largura - margem_x, y, money_str(debitos_total))
        y -= 7 * mm

        # Honorários e líquido
        c.setFont("Helvetica", 9)
        c.drawString(margem_x, y, f"Honorários ({(imv.honorarios_tipo or 'A').upper()} - {imv.honorarios_percentual or '0%'})")
        c.drawRightString(largura - margem_x, y, money_str(honorarios))
        y -= 6 * mm

        c.setFont("Helvetica-Bold", 10)
        c.drawString(margem_x, y, "Valor líquido do imóvel")
        c.drawRightString(largura - margem_x, y, money_str(liquido))
        y -= 10 * mm
        c.setFont("Helvetica", 9)

    # Rodapé com totais gerais
    quebra_pagina_se_preciso(45*mm)
    c.line(margem_x, y, largura - margem_x, y)
    y -= 9 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem_x, y, "TOTAL LÍQUIDO (geral)")
    c.drawRightString(largura - margem_x, y, money_str(total_liquido_geral))
    y -= 7 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margem_x, y, "TOTAL HONORÁRIOS (geral)")
    c.drawRightString(largura - margem_x, y, money_str(total_honorarios_geral))

    c.showPage()
    c.save()


def caminho_pdf_recibo_organizado(resumo) -> str:
    imv = resumo.get("imovel")
    imv_id = getattr(imv, "id", "X")
    desc = (getattr(imv, "descricao", "imovel") or "imovel").strip()

    # limpa nome pra não dar problema no Windows
    for ch in r'\/:*?"<>|':
        desc = desc.replace(ch, "-")
    desc = " ".join(desc.split())[:40] 

    mes = resumo.get("mes")
    ano = resumo.get("ano")
    periodo = f"{int(mes):02d}-{ano}" if mes and ano else datetime.now().strftime("%m-%Y")

    base = Path.home() / "Documents" / "ImobManager" / "Recibos"
    base.mkdir(parents=True, exist_ok=True)

    nome = f"Boleta_{periodo}_Imovel{imv_id}_{desc}.pdf"
    return str(base / nome)

def imprimir_recibo(resumo: dict):
    try:
        # 1) gera PDF temporário
        caminho_pdf = os.path.join(tempfile.gettempdir(), "boleta_aluguel_tmp.pdf")
        gerar_pdf_recibo_imovel(caminho_pdf, resumo)

        # 2) tenta Adobe (igual sua função atual do Window)
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_appdata = os.environ.get("LOCALAPPDATA", "")

        possiveis = [
            os.path.join(program_files, "Adobe", "Acrobat DC", "Acrobat", "Acrobat.exe"),
            os.path.join(program_files_x86, "Adobe", "Acrobat DC", "Acrobat", "Acrobat.exe"),
            os.path.join(program_files, "Adobe", "Acrobat Reader DC", "Reader", "AcroRd32.exe"),
            os.path.join(program_files_x86, "Adobe", "Acrobat Reader DC", "Reader", "AcroRd32.exe"),
            os.path.join(local_appdata, "Programs", "Adobe", "Acrobat Reader", "Acrobat", "Acrobat.exe"),
        ]

        exe = next((p for p in possiveis if p and os.path.exists(p)), None)

        if exe:
            subprocess.Popen([exe, "/t", caminho_pdf], close_fds=True)
            messagebox.showinfo("Imprimir", "Recibo enviado para a impressora padrão.")
            return caminho_pdf

        # 3) fallback: app padrão
        try:
            os.startfile(caminho_pdf, "print")
            messagebox.showinfo("Imprimir", "Recibo enviado para a impressora padrão.")
            return caminho_pdf
        except Exception:
            pass

        # 4) último fallback: abre o PDF
        os.startfile(caminho_pdf)
        messagebox.showinfo("Imprimir", "O PDF foi aberto.\nAperte Ctrl+P e clique em Imprimir.")
        return caminho_pdf

    except Exception as e:
        messagebox.showerror("Erro", f"Não foi possível imprimir/abrir o PDF:\n{e}")
        return None

def imprimir_pdf_temp(gerar_pdf_fn, temp_nome: str, *args, **kwargs):
    try:
        caminho_pdf = os.path.join(tempfile.gettempdir(), temp_nome)
        gerar_pdf_fn(caminho_pdf, *args, **kwargs)

        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_appdata = os.environ.get("LOCALAPPDATA", "")

        possiveis = [
            os.path.join(program_files, "Adobe", "Acrobat DC", "Acrobat", "Acrobat.exe"),
            os.path.join(program_files_x86, "Adobe", "Acrobat DC", "Acrobat", "Acrobat.exe"),
            os.path.join(program_files, "Adobe", "Acrobat Reader DC", "Reader", "AcroRd32.exe"),
            os.path.join(program_files_x86, "Adobe", "Acrobat Reader DC", "Reader", "AcroRd32.exe"),
            os.path.join(local_appdata, "Programs", "Adobe", "Acrobat Reader", "Acrobat", "Acrobat.exe"),
        ]

        exe = next((p for p in possiveis if p and os.path.exists(p)), None)

        if exe:
            subprocess.Popen([exe, "/t", caminho_pdf], close_fds=True)
            messagebox.showinfo("Imprimir", "Documento enviado para a impressora padrão.")
            return caminho_pdf

        try:
            os.startfile(caminho_pdf, "print")
            messagebox.showinfo("Imprimir", "Documento enviado para a impressora padrão.")
            return caminho_pdf
        except Exception:
            pass

        os.startfile(caminho_pdf)
        messagebox.showinfo("Imprimir", "O PDF foi aberto.\nAperte Ctrl+P e clique em Imprimir.")
        return caminho_pdf

    except Exception as e:
        messagebox.showerror("Erro", f"Falha ao gerar/imprimir PDF temporário:\n{e}")
        return None


# Modelos

@dataclass
class Locador:
    id: int
    nome: str
    cpf_cnpj: str = ""
    email: str = ""
    telefone: str = ""
    observacoes: str = ""
    endereco: str = ""
    bairro: str = ""
    cidade: str = ""
    cep: str = ""
    estado: str = ""
    

@dataclass
class Locatario:
    id: int
    nome: str
    cpf_cnpj: str = ""
    email: str = ""
    telefone: str = ""
    observacoes: str = ""
    garantia_tipo: str = "FIADOR" 
    # CAUÇÃO
    caucao_descricao: str = ""    
    caucao_valor: float = 0.0
    caucao_data: str = ""
    # Fiador
    fiador_nome: str = ""
    fiador_tel: str = ""
    fiador_cpf: str = ""
    fiador_email: str = ""
    # SEGURO FIANÇA
    seguro_instituicao: str = ""
    seguro_valor: float = 0.0
    seguro_data: str = ""
    # OBSERVAÇÕES DA GARANTIA
    garantia_obs: str = ""

@dataclass
class Imovel:
    id: int
    descricao: str
    endereco: str = ""
    proprietario_id: Optional[int] = None
    cidade: str = ""
    estado: str = ""
    cep: str = ""
    observacoes: str = ""
    bairro: str = ""
    locatario_id: Optional[int] = None
    honorarios_tipo: str = "A"       
    honorarios_percentual: str = ""    
    tipo_reajuste: str = ""  
    valor_aluguel: str = ""           
    data_inicio: str = ""            
    mes_reajuste: str = ""           
    dia_vencimento: str = ""  

@dataclass
class Ocorrencia:
    id: int
    imovel_id: int
    parte: str            # "LOCADOR" ou "LOCATARIO"
    pessoa_id: Optional[int] = None  
    pessoa_nome: str = ""            # cache do nome
    data: str = ""       
    tipo: str = "DEBITO" # "DEBITO" ou "CREDITO"
    valor: float = 0.0 
    descricao: str = ""

# Camada de dados

class DataStore:
    def __init__(self, data_dir: str = "data", autosave: bool = True) -> None:
        self._locadores: Dict[int, Locador] = {}
        self._locatarios: Dict[int, Locatario] = {}
        self._imoveis: Dict[int, Imovel] = {}
        self._ocorrencias: Dict[int, Ocorrencia] = {}

        self.data_dir = get_data_dir("ImobManager")

        self.backup_dir = self.data_dir / "backup"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.autosave = autosave

        self._locadores_csv = self.data_dir / "locadores.csv"
        self._locatarios_csv = self.data_dir / "locatarios.csv"
        self._imoveis_csv = self.data_dir / "imoveis.csv"
        self._ocorrencias_csv = self.data_dir / "ocorrencias.csv"

        self._ensure_csv(self._locadores_csv, list(Locador.__dataclass_fields__.keys()))
        self._ensure_csv(self._locatarios_csv, list(Locatario.__dataclass_fields__.keys()))
        self._ensure_csv(self._imoveis_csv, list(Imovel.__dataclass_fields__.keys()))
        self._ensure_csv(self._ocorrencias_csv, list(Ocorrencia.__dataclass_fields__.keys()))

        self._load_all()

        # id começa do max+1
        self._id_gen_locadores = itertools.count(
            max(self._locadores.keys(), default=0) + 1
        )
        self._id_gen_locatarios = itertools.count(
            max(self._locatarios.keys(), default=0) + 1
        )
        self._id_gen_imoveis = itertools.count(
            max(self._imoveis.keys(), default=0) + 1
        )
        self._id_gen_ocorrencias = itertools.count(
            max(self._ocorrencias.keys(), default=0) + 1
        )


    def _backup_file(self, path: Path):
        if not path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"{path.stem}_{timestamp}.csv"
        shutil.copy2(path, backup_path)

    # -- Utilidades CSV -- #

    def _read_csv(self, path: Path) -> list[dict]:
            if not path.exists():
                return []
            with path.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [dict(row) for row in reader]
            
    def _write_csv(self, path: Path, rows: list[dict], fieldnames: list[str]):
            self._backup_file(path)
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

    def _ensure_csv(self, path: Path, fieldnames: list[str]):
        """
        Garante que o CSV exista com cabeçalho.
        Se não existir, cria vazio (só header).
        """
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()    

    def _load_all(self):
            # locadores
            rows = self._read_csv(self._locadores_csv)
            for r in rows:
                obj = Locador(
                    id=int(r["id"]),
                    nome=r["nome"],
                    cpf_cnpj=r.get("cpf_cnpj",""),
                    email=r.get("email",""),
                    telefone=r.get("telefone",""),
                    observacoes=r.get("observacoes",""),
                    endereco=r.get("endereco",""), bairro=r.get("bairro",""),
                    cidade=r.get("cidade",""), cep=r.get("cep",""), estado=r.get("estado",""),
                )
                self._locadores[obj.id] = obj

            # locatarios
            rows = self._read_csv(self._locatarios_csv)
            for r in rows:
                obj = Locatario(
                    id=int(r["id"]),
                    nome=r["nome"],
                    cpf_cnpj=r.get("cpf_cnpj",""),
                    email=r.get("email",""),
                    telefone=r.get("telefone",""),
                    observacoes=r.get("observacoes",""),

                    garantia_tipo=(r.get("garantia_tipo","") or "").strip().upper() or "FIADOR",

                    caucao_descricao=r.get("caucao_descricao",""),
                    caucao_valor=float(r.get("caucao_valor", 0.0) or 0.0),
                    caucao_data=r.get("caucao_data",""),

                    fiador_nome=r.get("fiador_nome",""),
                    fiador_cpf=r.get("fiador_cpf",""),
                    fiador_tel=r.get("fiador_tel",""),
                    fiador_email=r.get("fiador_email",""),

                    seguro_instituicao=r.get("seguro_instituicao",""),
                    seguro_valor=float(r.get("seguro_valor", 0.0) or 0.0),
                    seguro_data=r.get("seguro_data",""),

                    garantia_obs=r.get("garantia_obs",""),
                )
                self._locatarios[obj.id] = obj

            # imoveis
            rows = self._read_csv(self._imoveis_csv)
            for r in rows:
                prop = r.get("proprietario_id","")
                proprietario_id = int(prop) if str(prop).strip().isdigit() else None
                loc = r.get("locatario_id","")
                locatario_id = int(loc) if str(loc).strip().isdigit() else None
                # --- valor_aluguel (garante float) ---
                raw_va = (r.get("valor_aluguel", "") or "").strip()
                try:
                    valor_aluguel = float(raw_va.replace(",", "."))
                except Exception:
                    valor_aluguel = parse_money(raw_va) if raw_va else 0.0
                obj = Imovel(
                    id=int(r["id"]),
                    descricao=r["descricao"],
                    endereco=r.get("endereco",""),
                    proprietario_id=proprietario_id,
                    cidade=r.get("cidade",""),
                    estado=r.get("estado",""),
                    cep=r.get("cep",""),
                    observacoes=r.get("observacoes",""),
                    bairro=r.get("bairro",""),
                    locatario_id=locatario_id,
                    honorarios_tipo=r.get("honorarios_tipo", "A"),
                    honorarios_percentual=r.get("honorarios_percentual",""),
                    tipo_reajuste=r.get("tipo_reajuste",""),
                    valor_aluguel=valor_aluguel,
                    data_inicio=r.get("data_inicio",""),
                    mes_reajuste=r.get("mes_reajuste",""),
                    dia_vencimento=r.get("dia_vencimento",""),
                )
                self._imoveis[obj.id] = obj
            
            # ocorrências
            rows = self._read_csv(self._ocorrencias_csv)
            for r in rows:
                try:
                    valor = float(r.get("valor", "0").replace(",", "."))
                except Exception:
                    valor = 0.0

                obj = Ocorrencia(
                    id         = int(r["id"]),
                    imovel_id  = int(r["imovel_id"]),
                    parte      = r.get("parte", ""),
                    pessoa_nome= r.get("pessoa_nome", ""),
                    data       = r.get("data", ""),
                    tipo       = r.get("tipo", ""),
                    valor      = valor,
                    descricao  = r.get("descricao", ""),
                )
                self._ocorrencias[obj.id] = obj

    def _save_locadores(self):
        rows = [asdict(x) for x in self._locadores.values()]
        fields = ["id","nome","cpf_cnpj","email","telefone","observacoes",
                  "endereco","bairro","cidade","cep","estado"]
        self._write_csv(self._locadores_csv, rows, fields)
        
    def _save_locatarios(self):
        rows = [asdict(x) for x in self._locatarios.values()]
        fields = ["id","nome","cpf_cnpj","email","telefone","observacoes","garantia_tipo",
                  "caucao_descricao","caucao_valor","caucao_data","fiador_nome","fiador_cpf",
                  "fiador_tel","fiador_email","seguro_instituicao","seguro_valor","seguro_data","garantia_obs",]
        # garante que cada linha só tenha as colunas do CSV
        rows = [{k: r.get(k, "") for k in fields} for r in rows]
        self._write_csv(self._locatarios_csv, rows, fields)

    def _save_imoveis(self):
        rows = []
        for x in self._imoveis.values():
            d = asdict(x)

            d["proprietario_id"] = "" if d.get("proprietario_id") is None else d.get("proprietario_id")
            d["locatario_id"]    = "" if d.get("locatario_id") is None else d.get("locatario_id")

            try:
                d["valor_aluguel"] = f"{float(d.get('valor_aluguel') or 0.0):.2f}"
            except Exception:
                d["valor_aluguel"] = "0.00"

            rows.append(d)

        fields = [
            "id","descricao","endereco","proprietario_id","cidade","estado","cep","observacoes",
            "bairro","locatario_id",
            "honorarios_tipo","honorarios_percentual",
            "tipo_reajuste","valor_aluguel","data_inicio","mes_reajuste","dia_vencimento"
        ]
        self._write_csv(self._imoveis_csv, rows, fields)
    
    def _save_ocorrencias(self) -> None:
        rows = []
        for o in self._ocorrencias.values():
            rows.append({
                "id":          o.id,
                "imovel_id":   o.imovel_id,
                "parte":       o.parte,
                "pessoa_nome": o.pessoa_nome,
                "data":        o.data,
                "tipo":        o.tipo,
                "valor":       f"{o.valor:.2f}",
                "descricao":   o.descricao,
            })
        self._write_csv(
            self._ocorrencias_csv,
            rows,
            fieldnames=["id", "imovel_id", "parte", "pessoa_nome",
                        "data", "tipo", "valor", "descricao"],
        )

    def calcular_resumo_imovel_periodo(self, imovel_id: int, mes: int, ano: int, parte: str = "locatario") -> dict:
        imv = self.get_imovel(imovel_id)
        if not imv:
            raise ValueError("Imóvel não encontrado.")

        # --- Valor do aluguel ---
        try:
            aluguel = float(str(imv.valor_aluguel).replace(",", "."))
        except:
            aluguel = 0.0

        def _norm_parte(p: str) -> str:
            p = (p or "").strip().lower()
            if p.startswith("locat"):
                return "locatario"
            if p.startswith("locad"):
                return "locador"
            return p

        parte_filtro = _norm_parte(parte) if parte else "todos"

        # --- Selecionar só as ocorrências do mês/ano informados + filtro por parte ---
        ocorrs_periodo = []
        for oc in self.list_ocorrencias():
            if oc.imovel_id != imovel_id:
                continue

            # filtra a parte (locatario/locador) quando for o caso
            if parte_filtro in ("locatario", "locador"):
                if _norm_parte(getattr(oc, "parte", "")) != parte_filtro:
                    continue

            # data no formato DD/MM/YYYY
            try:
                d, m, a = map(int, oc.data.split("/"))
            except:
                continue

            if m == mes and a == ano:
                ocorrs_periodo.append(oc)

        # --- Somatórios ---
        total_debitos = sum(oc.valor for oc in ocorrs_periodo if str(oc.tipo).lower().startswith("d"))
        total_creditos = sum(oc.valor for oc in ocorrs_periodo if str(oc.tipo).lower().startswith("c"))

        total_a_pagar = aluguel + total_debitos - total_creditos

        return {
            "imovel": imv,
            "valor_aluguel": aluguel,
            "ocorrencias": ocorrs_periodo,
            "total_debitos": total_debitos,
            "total_creditos": total_creditos,
            "total_a_pagar": total_a_pagar,
            "mes": mes,
            "ano": ano,
            "parte": parte_filtro, 
        }


        # --Locadores-- #
    def create_locador(self, payload: Dict[str, Any]) -> Locador:
        new_id = next(self._id_gen_locadores)
        obj = Locador(id=new_id, **payload)
        self._locadores[new_id] = obj
        if self.autosave: self._save_locadores()
        return obj

    def update_locador(self, obj_id: int, payload: Dict[str, Any]) -> Optional[Locador]:
        if obj_id not in self._locadores:
            return None
        locador = self._locadores[obj_id]
        for k,v in payload.items(): setattr(locador, k, v)
        if self.autosave: self._save_locadores()
        return locador

    def delete_locador(self, obj_id: int) -> None:
        self._locadores.pop(obj_id, None)
        if self.autosave: self._save_locadores()

    def list_locadores(self) -> List[Locador]:
        return list(self._locadores.values())
    
        # --Locatarios-- #

    def create_locatario(self, payload: Dict[str, Any]) -> Locatario:
        new_id = next(self._id_gen_locatarios)
        obj = Locatario(id=new_id, **payload)
        self._locatarios[new_id] = obj
        if self.autosave: self._save_locatarios()
        return obj
    
    def update_locatario(self, obj_id: int, payload: Dict[str, Any]) -> Optional[Locatario]:
        if obj_id not in self._locatarios:
            return None
        locatario = self._locatarios[obj_id]
        for k,v in payload.items(): setattr(locatario, k, v)
        if self.autosave: self._save_locatarios()
        return locatario
    
    def delete_locatario(self, obj_id: int) -> None:
        self._locatarios.pop(obj_id, None)
        if self.autosave: self._save_locatarios()

    def list_locatarios(self) -> List[Locatario]:
        return list(self._locatarios.values())
    
        # --Imoveis-- #
    def create_imovel(self, payload: Dict[str, Any]) -> Imovel:
        new_id = next(self._id_gen_imoveis)
        obj = Imovel(id=new_id, **payload)
        self._imoveis[new_id] = obj
        if self.autosave: self._save_imoveis()
        return obj
    
    def update_imovel(self, obj_id: int, payload: Dict[str, Any]) -> Optional[Imovel]:
        if obj_id not in self._imoveis:
            return None
        imovel= self._imoveis[obj_id]
        for k,v in payload.items(): setattr(imovel, k, v)
        if self.autosave: self._save_imoveis()
        return imovel
    
    def delete_imovel(self, obj_id) -> None:
        self._imoveis.pop(obj_id, None)
        if self.autosave: self._save_imoveis()

    def list_imoveis(self) -> List[Imovel]:
        return list(self._imoveis.values())

        # --Ocorrências--
    def add_ocorrencia(self, occ: Ocorrencia) -> Ocorrencia:
        if not getattr(occ, "id", 0):
            occ.id = next(self._id_gen_ocorrencias)

        self._ocorrencias[occ.id] = occ
        if self.autosave:
            self._save_ocorrencias()
        return occ
    
    def update_ocorrencia(self, occ: Ocorrencia) -> None:
        self._ocorrencias[occ.id] = occ
        if self.autosave:
            self._save_ocorrencias()

    def delete_ocorrencia(self, occ_id: int) -> None:
        if occ_id in self._ocorrencias:
            del self._ocorrencias[occ_id]
            if self.autosave:
                self._save_ocorrencias()

    def list_ocorrencias(self) -> list[Ocorrencia]:
        return list(self._ocorrencias.values())

    def get_ocorrencia(self, occ_id: int) -> Optional[Ocorrencia]:
        return self._ocorrencias.get(occ_id)
    
    def get_imovel(self, imovel_id: int) -> Optional[Imovel]:
        return self._imoveis.get(imovel_id)

    def get_locatario(self, loc_id: int) -> Optional[Locatario]:
        return self._locatarios.get(loc_id)

    def get_locador(self, locador_id: int) -> Optional[Locador]:
        return self._locadores.get(locador_id)


# Componentes reutilizáveis #

class SearchBar(ttk.Frame):
    def __init__(self, master, on_change: Callable[[str], None]):
        super().__init__(master)
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_clear = ttk.Button(self, text="Limpar", command=self.clear)
        self.btn_clear.pack(side=tk.LEFT)
        self.var.trace_add("write", lambda *_: on_change(self.var.get()))

    def clear(self):
        self.var.set("")

class ListWithActions(ttk.Frame):
    def __init__(self, master, columns: List[tuple], on_new, on_edit, on_delete):
        super().__init__(master)
        # Search #
        self.search = SearchBar(self, on_change=self._apply_filter)
        self.search.pack(fill=tk.X, pady=(0, 6))

        #Tree
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", height=12)
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=tk.W)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0))

        #Actions
        actions = ttk.Frame(self)
        actions.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        ttk.Button(actions, text="Novo", command=on_new).pack(fill=tk.X)
        ttk.Button(actions, text="Editar", command=lambda: on_edit(self._selected_id())).pack(fill=tk.X, pady=6)
        ttk.Button(actions, text="Excluir", command=lambda: on_delete(self._selected_id())).pack(fill=tk.X)
        
        self._all_rows: List[Dict[str, Any]] = []

    def _selected_id(self) -> Optional[int]:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(self.tree.item(sel[0], "values")[0])
    
    def get_selected_id(self) -> Optional[int]:
        return self._selected_id()

    def set_rows(self, rows: List[Dict[str, Any]]):
        self._all_rows = rows 
        self._reload_tree(rows)

    def _reload_tree(self, rows: List[Dict[str, Any]]):
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", tk.END, values=[r[k] for k in self.tree["columns"]])

    def _apply_filter(self, term: str):
        term_low = term.strip().lower()
        if not term_low:
            self._reload_tree(self._all_rows)
            return
        filt = []
        for r in self._all_rows:
            if any(term_low in str(v).lower() for v in r.values()):
                filt.append(r)
        self._reload_tree(filt)

# Formularios #

class BaseForm(tk.Toplevel):
    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.geometry("+200+120")
        self.grab_set()
        self.result: Optional[Dict[str, Any]] = None

    def _add_labeled_entry(self, parent, label: str, var: tk.StringVar, width=46):
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=label).pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=var, width=width).pack(fill=tk.X)
        frame.pack(fill=tk.X, pady=4)

    def _btns(self, on_ok: Callable, on_cancel: Callable):
        bar = ttk.Frame(self)
        ttk.Button(bar, text="Cancelar", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Salvar", command=on_ok).pack(side=tk.RIGHT, padx=6)
        bar.pack(fill=tk.X, padx=8)

class ScrollableFrame(ttk.Frame):
    def __init__(self, master, *, width=None, height=None, padding=(0, 0), **kwargs):
        super().__init__(master, **kwargs)

        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        if width: self.canvas.config(width=width)
        if height: self.canvas.config(height=height)

        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.inner = ttk.Frame(self.canvas)
        self.body_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.body = ttk.Frame(self.inner)
        self.body.pack(fill=tk.BOTH, expand=True, padx=padding[0], pady=padding[1])
        self._pad_left = padding[0]
        self._pad_top  = padding[1]
        self.body.pack(fill=tk.BOTH, expand=True,
                padx=(self._pad_left, self._pad_left),  
                pady=self._pad_top)

        self.body.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._bind_mousewheel()

    def _on_frame_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.update_idletasks()
        sbw = self.vsb.winfo_width() or self.vsb.winfo_reqwidth()

        GAP_RIGHT = 4

        new_w = max(event.width - sbw - GAP_RIGHT, 0)
        self.canvas.itemconfig(self.body_id, width=new_w)

        self.body.pack_configure(padx=(self._pad_left, self._pad_left + GAP_RIGHT))
    
    def _bind_mousewheel(self):
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.canvas.yview_scroll(delta, "units")

class LocadorForm(BaseForm):
    def __init__(self, master, initial: Optional[Locador] = None):
        super().__init__(master, "Locador")
        self.var_nome = tk.StringVar(value=getattr(initial, "nome", ""))
        self.var_cpf = tk.StringVar(value=getattr(initial, "cpf_cnpj", ""))
        self.var_email = tk.StringVar(value=getattr(initial, "email", ""))
        self.var_tel = tk.StringVar(value=getattr(initial, "telefone", ""))
        self.var_obs = tk.StringVar(value=getattr(initial, "observacoes", ""))

        # Endereço do Locador
        self.var_end_loc = tk.StringVar(value=getattr(initial, "endereco", ""))
        self.var_bairro_loc = tk.StringVar(value=getattr(initial, "bairro", ""))
        self.var_cidade_loc = tk.StringVar(value=getattr(initial, "cidade", ""))
        self.var_cep_loc = tk.StringVar(value=getattr(initial, "cep", ""))
        self.var_estado_loc = tk.StringVar(value=getattr(initial, "estado", ""))

        scroll = ScrollableFrame(self, height=450, padding=(15, 12))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        # Campos simples
        self._add_labeled_entry(body, "Nome", self.var_nome)
        self._add_labeled_entry(body, "E-mail", self.var_email)

        # --- Documento (CPF/CNPJ) com máscara ---
        doc_frame = ttk.Frame(body)
        ttk.Label(doc_frame, text="Documento").pack(anchor=tk.W)

        row = ttk.Frame(doc_frame)
        row.pack(fill=tk.X)

        doc_digits = "".join(ch for ch in self.var_cpf.get() if ch.isdigit())
        default_doc_type = "CNPJ" if len(doc_digits) > 11 else "CPF"
        self.var_doc_type = tk.StringVar(value=default_doc_type)

        ttk.Combobox(
            row, state="readonly", width=6, textvariable=self.var_doc_type,
            values=("CPF", "CNPJ")
        ).pack(side=tk.LEFT, padx=(0, 6))

        def _doc_mask(s: str) -> str:
            return format_cpf(s) if self.var_doc_type.get() == "CPF" else format_cnpj(s)

        self.doc_entry = MaskedEntry(row, _doc_mask, textvariable=self.var_cpf, width=38)
        self.doc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        doc_frame.pack(fill=tk.X, pady=4)

        # Atualiza a máscara quando muda CPF/CNPJ
        self.var_doc_type.trace_add("write", lambda *_: self.doc_entry._apply_mask())

        # --- Telefone com máscara ---
        tel_frame = ttk.Frame(body)
        ttk.Label(tel_frame, text="Telefone").pack(anchor=tk.W)
        self.tel_entry = MaskedEntry(tel_frame, format_telefone, textvariable=self.var_tel, width=46)
        self.tel_entry.pack(fill=tk.X)
        tel_frame.pack(fill=tk.X, pady=4)

        # --- Endereço do Locador ---
        self._add_labeled_entry(body, "Endereço", self.var_end_loc)
        self._add_labeled_entry(body, "Bairro",    self.var_bairro_loc)
        self._add_labeled_entry(body, "Cidade",    self.var_cidade_loc)

        ttk.Label(body, text="CEP").pack(anchor=tk.W)
        cep_loc_frame = ttk.Frame(body)
        self.cep_loc_entry = MaskedEntry(cep_loc_frame, format_cep, textvariable=self.var_cep_loc, width=46)
        self.cep_loc_entry.pack(fill=tk.X)
        cep_loc_frame.pack(fill=tk.X, pady=4)

        self._add_labeled_entry(body, "Estado", self.var_estado_loc)

        # Observações
        self._add_labeled_entry(body, "Observações", self.var_obs)

        self.doc_entry._apply_mask()
        self.tel_entry._apply_mask()

        #Botões
        self._btns(self._on_ok, self.destroy)

    def _on_ok(self):
        nome = self.var_nome.get().strip()
        if not nome:
            messagebox.showwarning("Validação", "O campo Nome é obrigatório.")
            return
        self.result = dict(
            nome=nome,
            cpf_cnpj=self.var_cpf.get().strip(),
            email=self.var_email.get().strip(),
            telefone=self.var_tel.get().strip(),
            observacoes=self.var_obs.get().strip(),
            endereco=self.var_end_loc.get().strip(),
            bairro=self.var_bairro_loc.get().strip(),
            cidade=self.var_cidade_loc.get().strip(),
            cep=self.var_cep_loc.get().strip(),
            estado=self.var_estado_loc.get().strip(),
        )
        self.destroy()

class LocatarioForm(BaseForm):
    def __init__(self, master, initial: Optional[Locatario] = None):
        super().__init__(master, "Locatario")
        self.var_nome = tk.StringVar(value=getattr(initial, "nome", ""))
        self.var_cpf = tk.StringVar(value=getattr(initial, "cpf_cnpj", ""))
        self.var_email = tk.StringVar(value=getattr(initial, "email", ""))
        self.var_tel = tk.StringVar(value=getattr(initial, "telefone", ""))
        self.var_obs = tk.StringVar(value=getattr(initial, "observacoes", ""))

        self.garantia_tipo_var = tk.StringVar(
        value=(getattr(initial, "garantia_tipo", "") or "FIADOR").strip().upper()
        )

        # CAUÇÃO
        self.caucao_desc_var = tk.StringVar(value=getattr(initial, "caucao_descricao", ""))
        self.caucao_valor_var = tk.StringVar(value=format_money(str(int(round(float(getattr(initial, "caucao_valor", 0.0) or 0.0)*100))) if float(getattr(initial, "caucao_valor", 0.0) or 0.0) else ""))
        self.caucao_data_var = tk.StringVar(value=getattr(initial, "caucao_data", ""))

        # FIADOR
        self.fiador_nome_var = tk.StringVar(value=getattr(initial, "fiador_nome", ""))
        self.fiador_cpf_var  = tk.StringVar(value=getattr(initial, "fiador_cpf", ""))
        self.fiador_tel_var  = tk.StringVar(value=getattr(initial, "fiador_tel", ""))
        self.fiador_email_var= tk.StringVar(value=getattr(initial, "fiador_email", ""))

        # SEGURO
        self.seguro_inst_var = tk.StringVar(value=getattr(initial, "seguro_instituicao", ""))
        self.seguro_valor_var= tk.StringVar(value=format_money(str(int(round(float(getattr(initial, "seguro_valor", 0.0) or 0.0)*100))) if float(getattr(initial, "seguro_valor", 0.0) or 0.0) else ""))
        self.seguro_data_var = tk.StringVar(value=getattr(initial, "seguro_data", ""))

        # OBS garantia
        self.garantia_obs_var = tk.StringVar(value=getattr(initial, "garantia_obs", ""))

        scroll = ScrollableFrame(self, height=450, padding=(15, 12))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        ttk.Label(body, text="Locatário").pack(anchor=tk.W, pady=(8, 2))
        # Campos simples
        self._add_labeled_entry(body, "Nome", self.var_nome)
        self._add_labeled_entry(body, "E-mail", self.var_email)

        # --- Documento (CPF/CNPJ) com máscara dinâmica ---
        doc_frame = ttk.Frame(body)
        ttk.Label(doc_frame, text="Documento").pack(anchor=tk.W)

        row = ttk.Frame(doc_frame)
        row.pack(fill=tk.X)

        doc_digits = "".join(ch for ch in self.var_cpf.get() if ch.isdigit())
        default_doc_type = "CNPJ" if len(doc_digits) > 11 else "CPF"
        self.var_doc_type = tk.StringVar(value=default_doc_type)
        
        ttk.Combobox(
            row, state="readonly", width=6, textvariable=self.var_doc_type,
            values=("CPF", "CNPJ")
        ).pack(side=tk.LEFT, padx=(0, 6))
        
        def _doc_mask(s: str) -> str:
            return format_cpf(s) if self.var_doc_type.get() == "CPF" else format_cnpj(s)

        self.doc_entry = MaskedEntry(row, _doc_mask, textvariable=self.var_cpf, width=38)
        self.doc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        doc_frame.pack(fill=tk.X, pady=4)

        # Reaplica a máscara ao trocar CPF/CNPJ
        self.var_doc_type.trace_add("write", lambda *_: self.doc_entry._apply_mask())

        # --- Telefone com máscara ---
        tel_frame = ttk.Frame(body)
        ttk.Label(tel_frame, text="Telefone").pack(anchor=tk.W)
        self.tel_entry = MaskedEntry(tel_frame, format_telefone, textvariable=self.var_tel, width=46)
        self.tel_entry.pack(fill=tk.X)
        tel_frame.pack(fill=tk.X, pady=4)

        # GARANTIA (CAUÇÃO / FIADOR / SEGURO FIANÇA)

        ttk.Label(body, text="Garantia").pack(anchor=tk.W, pady=(10, 2))

        # Seletor do tipo
        tipo_frame = ttk.Frame(body)
        ttk.Label(tipo_frame, text="Tipo de garantia").pack(anchor=tk.W)
        ttk.Combobox(
            tipo_frame,
            state="readonly",
            width=20,
            textvariable=self.garantia_tipo_var,
            values=("CAUCAO", "FIADOR", "SEGURO_FIANCA"),
        ).pack(fill=tk.X)
        tipo_frame.pack(fill=tk.X, pady=4)

        # Frames 
        self.frm_caucao = ttk.LabelFrame(body, text="Caução")
        self.frm_fiador = ttk.LabelFrame(body, text="Fiador")
        self.frm_seguro = ttk.LabelFrame(body, text="Seguro fiança")

        # --------- CAUÇÃO ----------
        ttk.Label(self.frm_caucao, text="Descrição").pack(anchor=tk.W)
        ttk.Entry(self.frm_caucao, textvariable=self.caucao_desc_var, width=46).pack(fill=tk.X)
        ttk.Label(self.frm_caucao, text="Valor").pack(anchor=tk.W)
        MaskedEntry(self.frm_caucao, format_money, textvariable=self.caucao_valor_var, width=46).pack(fill=tk.X)
        ttk.Label(self.frm_caucao, text="Data").pack(anchor=tk.W)
        MaskedEntry(self.frm_caucao, format_date, textvariable=self.caucao_data_var, width=46).pack(fill=tk.X)

        # --------- FIADOR ----------
        ttk.Label(self.frm_fiador, text="Nome").pack(anchor=tk.W)
        ttk.Entry(self.frm_fiador, textvariable=self.fiador_nome_var, width=46).pack(fill=tk.X)

        ttk.Label(self.frm_fiador, text="CPF").pack(anchor=tk.W)
        self.fiador_cpf_entry = MaskedEntry(self.frm_fiador, format_cpf, textvariable=self.fiador_cpf_var, width=46)
        self.fiador_cpf_entry.pack(fill=tk.X)

        ttk.Label(self.frm_fiador, text="Telefone").pack(anchor=tk.W)
        self.fiador_tel_entry = MaskedEntry(self.frm_fiador, format_telefone, textvariable=self.fiador_tel_var, width=46)
        self.fiador_tel_entry.pack(fill=tk.X)

        ttk.Label(self.frm_fiador, text="E-mail").pack(anchor=tk.W)
        ttk.Entry(self.frm_fiador, textvariable=self.fiador_email_var, width=46).pack(fill=tk.X)

        # --------- SEGURO FIANÇA ----------
        ttk.Label(self.frm_seguro, text="Instituição financeira").pack(anchor=tk.W)
        ttk.Entry(self.frm_seguro, textvariable=self.seguro_inst_var, width=46).pack(fill=tk.X)

        ttk.Label(self.frm_seguro, text="Valor").pack(anchor=tk.W)
        MaskedEntry(self.frm_seguro, format_money, textvariable=self.seguro_valor_var, width=46).pack(fill=tk.X)

        ttk.Label(self.frm_seguro, text="Data").pack(anchor=tk.W)
        MaskedEntry(self.frm_seguro, format_date, textvariable=self.seguro_data_var, width=46).pack(fill=tk.X)

        # Observações da garantia (comum)
        ttk.Label(body, text="Observações da garantia").pack(anchor=tk.W, pady=(8, 2))
        ttk.Entry(body, textvariable=self.garantia_obs_var, width=46).pack(fill=tk.X, pady=(0, 8))


        def _mostrar_garantia(*_):
            # esconde tudo
            self.frm_caucao.pack_forget()
            self.frm_fiador.pack_forget()
            self.frm_seguro.pack_forget()

            tipo = (self.garantia_tipo_var.get() or "FIADOR").strip().upper()

            if tipo == "CAUCAO":
                self.frm_caucao.pack(fill=tk.X, pady=6)
            elif tipo == "SEGURO_FIANCA":
                self.frm_seguro.pack(fill=tk.X, pady=6)
            else:
                self.frm_fiador.pack(fill=tk.X, pady=6)

        # muda quando seleciona
        self.garantia_tipo_var.trace_add("write", _mostrar_garantia)

        # mostra o frame inicial
        _mostrar_garantia()

        # máscaras do fiador (se existirem)
        try:
            self.fiador_cpf_entry._apply_mask()
            self.fiador_tel_entry._apply_mask()
        except Exception:
            pass

        # Observações
        self._add_labeled_entry(body, "Observações", self.var_obs)

        # Aplica máscaras iniciais (campos fixos)
        self.doc_entry._apply_mask()
        self.tel_entry._apply_mask()

        # Botões
        self._btns(self._on_ok, self.destroy)


    def _on_ok(self):
        nome = self.var_nome.get().strip()
        if not nome:
            messagebox.showwarning("Validação", "O campo Nome é obrigatório.")
            return

        tipo = (self.garantia_tipo_var.get() or "FIADOR").strip().upper()

        # base
        data = dict(
            nome=nome,
            cpf_cnpj=self.var_cpf.get().strip(),
            email=self.var_email.get().strip(),
            telefone=self.var_tel.get().strip(),
            observacoes=self.var_obs.get().strip(),

            garantia_tipo=tipo,
            garantia_obs=self.garantia_obs_var.get().strip(),

            # limpa tudo por padrão
            caucao_descricao="",
            caucao_valor=0.0,
            caucao_data="",

            fiador_nome="",
            fiador_cpf="",
            fiador_tel="",
            fiador_email="",

            seguro_instituicao="",
            seguro_valor=0.0,
            seguro_data="",
        )

        if tipo == "CAUCAO":
            data["caucao_descricao"] = self.caucao_desc_var.get().strip()
            data["caucao_valor"] = parse_money(self.caucao_valor_var.get())
            data["caucao_data"] = self.caucao_data_var.get().strip()

        elif tipo == "SEGURO_FIANCA":
            data["seguro_instituicao"] = self.seguro_inst_var.get().strip()
            data["seguro_valor"] = parse_money(self.seguro_valor_var.get())
            data["seguro_data"] = self.seguro_data_var.get().strip()

        else:  # FIADOR
            data["fiador_nome"] = self.fiador_nome_var.get().strip()
            data["fiador_cpf"] = self.fiador_cpf_var.get().strip()
            data["fiador_tel"] = self.fiador_tel_var.get().strip()
            data["fiador_email"] = self.fiador_email_var.get().strip()

        self.result = data
        self.destroy()

class ImovelForm(BaseForm):
    def __init__(self, master, initial: Optional[Imovel] = None, owners: List[Locador] = None):
        super().__init__(master, "Imóvel")
        self.var_desc = tk.StringVar(value=getattr(initial, "descricao", ""))
        self.var_end = tk.StringVar(value=getattr(initial, "endereco", ""))
        self.var_cidade = tk.StringVar(value=getattr(initial, "cidade", ""))
        self.var_estado = tk.StringVar(value=getattr(initial, "estado", ""))
        self.var_cep = tk.StringVar(value=getattr(initial, "cep", ""))
        self.var_obs = tk.StringVar(value=getattr(initial, "observacoes", ""))
        self.var_bairro = tk.StringVar(value=getattr(initial, "bairro", ""))

        self.owners = owners or []
        self.var_owners = tk.StringVar()
        self.var_tenant = tk.StringVar()

        self.var_reaj = tk.StringVar(value=getattr(initial, "tipo_reajuste", "Anual"))
        self.var_dini = tk.StringVar(value=getattr(initial, "data_inicio", ""))
        self.var_mes  = tk.StringVar(value=getattr(initial, "mes_reajuste", "janeiro"))
        self.var_dia  = tk.StringVar(value=getattr(initial, "dia_vencimento", "5"))
        self.var_hon_tipo = tk.StringVar(value=getattr(initial, "honorarios_tipo", "A") or "A")
        self.var_hon = tk.StringVar(value=getattr(initial, "honorarios_percentual", "") or "")
        
        
        # Layout com scroll
        scroll = ScrollableFrame(self, height=450, padding=(15, 12))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        # Campos principais
        self._add_labeled_entry(body, "Descrição", self.var_desc)
        self._add_labeled_entry(body, "Endereço", self.var_end)
        self._add_labeled_entry(body, "Cidade", self.var_cidade)
        self._add_labeled_entry(body, "Estado", self.var_estado)
        self._add_labeled_entry(body, "Bairro", self.var_bairro)

        # Proprietario #
        ttk.Label(body, text="Proprietário").pack(anchor=tk.W)
        values_owners = [f"{o.id} - {o.nome}" for o in self.owners]

        cmb_owner = ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_owners, 
            values=values_owners,
            width=40,
        )
        cmb_owner.pack(fill=tk.X, pady=4)

        # pré-seleção no modo edição
        if initial and getattr(initial, "proprietario_id", None):
            oid = initial.proprietario_id
            for txt in values_owners:
                if txt.startswith(f"{oid} "): 
                    self.var_owners.set(txt)
                    break

        # CEP
        cep_frame = ttk.Frame(body)
        ttk.Label(cep_frame, text="CEP").pack(anchor=tk.W)
        self.cep_entry = MaskedEntry(cep_frame, format_cep, textvariable=self.var_cep, width=46)
        self.cep_entry.pack(fill=tk.X)
        cep_frame.pack(fill=tk.X, pady=4)

        # ------ Locatário ------
        ttk.Label(body, text="Locatário").pack(anchor=tk.W)

        locatarios = self.master.store.list_locatarios()
        values_loc = [f"{lt.id} - {lt.nome}" for lt in locatarios]

        cmb_tenant = ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_tenant,
            values=values_loc,
            width=40,
        )
        cmb_tenant.pack(fill=tk.X, pady=4)

        # Pré-seleção do locatário no modo edição
        if initial and getattr(initial, "locatario_id", None):
            lid = initial.locatario_id
            for lt in locatarios:
                if lt.id == lid:
                    self.var_tenant.set(f"{lt.id} - {lt.nome}")
                    break
        
        # ------ Honorários ------
        ttk.Label(body, text="Honorário (tipo)").pack(anchor=tk.W)
        ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_hon_tipo,
            values=("A", "C"),
            width=10,
        ).pack(fill=tk.X, pady=4)

        ttk.Label(body, text="Honorário (%)").pack(anchor=tk.W)
        hon_frame = ttk.Frame(body)
        MaskedEntry(hon_frame, format_percent, textvariable=self.var_hon, width=46).pack(fill=tk.X)
        hon_frame.pack(fill=tk.X, pady=4)

        # Tipo de Reajuste
        ttk.Label(body, text="Tipo de reajuste").pack(anchor=tk.W)
        ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_reaj,
            values=("Anual", "Semestral"),
            width=20,
        ).pack(fill=tk.X, pady=4)

        # Valor do Aluguel
        ttk.Label(body, text="Valor do aluguel").pack(anchor=tk.W)

        raw_inicial = getattr(initial, "valor_aluguel", 0.0) if initial else 0.0

        if isinstance(raw_inicial, (int, float)):
            base_val = float(raw_inicial)
        else:
            base_val = parse_money(str(raw_inicial))

        # converte para centavos e monta a string crua usada pela máscara
        centavos = int(round(base_val * 100))
        raw_valor = str(centavos) if centavos else ""

        self.var_val = tk.StringVar(
            value=format_money(raw_valor) if raw_valor else ""
        )

        alug_frame = ttk.Frame(body)
        self.alug_entry = MaskedEntry(
            alug_frame, format_money, textvariable=self.var_val, width=46
        )
        self.alug_entry.pack(fill=tk.X)
        alug_frame.pack(fill=tk.X, pady=4)

        # Data de inicio
        ttk.Label(body, text="Data de início (dd/mm/aaaa)").pack(anchor=tk.W)
        self.var_dini = tk.StringVar(value=getattr(initial, "data_inicio", ""))
        din_frame = ttk.Frame(body)
        self.dini_entry = MaskedEntry(din_frame, format_date, textvariable=self.var_dini, width=46)
        self.dini_entry.pack(fill=tk.X)
        din_frame.pack(fill=tk.X, pady=4)

        # Mês de reajuste
        meses = ("janeiro","fevereiro","março","abril","maio","junho",
                "julho","agosto","setembro","outubro","novembro","dezembro")
        
        ttk.Label(body, text="Mês de reajuste").pack(anchor=tk.W)
        self.var_mes = tk.StringVar(value=getattr(initial, "mes_reajuste", "janeiro"))
        ttk.Combobox(body, state="readonly", textvariable=self.var_mes, values=meses, width=20)\
            .pack(fill=tk.X, pady=4)

        # Dia de Vencimento
        ttk.Label(body, text="Dia de vencimento").pack(anchor=tk.W)
        self.var_dia = tk.StringVar(value=getattr(initial, "dia_vencimento", "5"))
        ttk.Spinbox(body, from_=1, to=31, textvariable=self.var_dia, width=10).pack(fill=tk.X, pady=4)

        # Observações
        self._add_labeled_entry(body, "Observações", self.var_obs)

        # Botões
        self._btns(self._on_ok, self.destroy)
    
    def _on_ok(self):
        desc = self.var_desc.get().strip()
        if not desc:
            messagebox.showwarning("Validação", "O campo descrição é obrigatório.")
            return

        # Proprietário (combobox "id - nome")
        owner_id: Optional[int] = None
        txt_owner = (self.var_owners.get() or "").strip()
        if txt_owner:
            try:
                owner_id = int(txt_owner.split("-", 1)[0].strip())
            except ValueError:
                owner_id = None

        # Locatário (combobox "id - nome")
        tenant_id: Optional[int] = None
        txt_tenant = (self.var_tenant.get() or "").strip() if hasattr(self, "var_tenant") else ""
        if txt_tenant:
            try:
                tenant_id = int(txt_tenant.split("-", 1)[0].strip())
            except ValueError:
                tenant_id = None

        self.result = dict(
            descricao      = desc,
            endereco       = self.var_end.get().strip(),
            cidade         = self.var_cidade.get().strip(),
            estado         = self.var_estado.get().strip(),
            bairro         = (self.var_bairro.get().strip() if hasattr(self, "var_bairro") else ""),
            cep            = self.var_cep.get().strip(),
            proprietario_id= owner_id,
            locatario_id   = tenant_id,
            honorarios_tipo = (self.var_hon_tipo.get().strip().upper() if hasattr(self, "var_hon_tipo") else "A"),
            honorarios_percentual = (self.var_hon.get().strip() if hasattr(self, "var_hon") else ""),
            tipo_reajuste  = (self.var_reaj.get().strip() if hasattr(self, "var_reaj") else ""),
            valor_aluguel  = parse_money(self.var_val.get()),
            data_inicio    = (self.var_dini.get().strip() if hasattr(self, "var_dini") else ""),
            mes_reajuste   = (self.var_mes.get().strip() if hasattr(self, "var_mes") else ""),
            dia_vencimento = (self.var_dia.get().strip() if hasattr(self, "var_dia") else ""),
            observacoes    = self.var_obs.get().strip(),
        )

        self.destroy()


class OcorrenciaForm(BaseForm):
    def __init__(
            self,
            master,
            store: DataStore,
            initial: Optional[Ocorrencia] = None,
    ):
        super().__init__(master, "Ocorrência")
        self.store = store
        self._initial_id = getattr(initial, "id", 0)

        # Variaveis
        self.var_imovel = tk.StringVar()

        self.var_parte = tk.StringVar(
            value=getattr(initial, "parte", "Locatário")
        )

        self.var_pessoa_nome = tk.StringVar(
            value=getattr(initial, "pessoa_nome", "")
        )

        # Data
        hoje = datetime.now().strftime("%d/%m/%Y")
        self.var_data = tk.StringVar(
            value=getattr(initial, "data", hoje)
        )

        # Tipo Credito/Debito
        self.var_tipo = tk.StringVar(
            value=getattr(initial, "tipo", "Debito")
        )

        # Valor
        raw_valor = ""
        if initial is not None:
            centavos = int(round(getattr(initial, "valor", 0.0) * 100))
            raw_valor = str(centavos)

        self.var_valor = tk.StringVar(
            value=format_money(raw_valor)
        )

        # Descrição
        self.var_descricao = tk.StringVar(
            value=getattr(initial, "descricao", "")
        )

        # --------- Layout ---------
        body = tk.Frame(self)
        body.pack(padx=15, pady=15, fill=tk.BOTH, expand=True)

        # Imóvel
        ttk.Label(body, text="Imóvel").pack(anchor=tk.W)
        self._imoveis_list = self.store.list_imoveis()
        imovel_values = [
            f"{imv.id} - {imv.descricao} ({imv.endereco})"
            for imv in self._imoveis_list
        ]
        self.cmb_imovel = ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_imovel,
            values=imovel_values,
        )
        self.cmb_imovel.pack(fill=tk.X, pady=4)

        # Se estiver editando, seleciona o imóvel correspondente
        if initial:
            for s in imovel_values:
                if s.startswith(f"{initial.imovel_id}"):
                    self.var_imovel.set(s)
                    break

        # Parte (Locatário / Locador)
        ttk.Label(body, text="Parte").pack(anchor=tk.W)
        self.cmb_parte = ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_parte,
            values=("Locatário", "Locador"),
            width=15,
        )
        self.cmb_parte.pack(fill=tk.X, pady=4)

        # Nome (pode ser preenchido automaticamente)
        self._add_labeled_entry(body, "Nome", self.var_pessoa_nome)

        # Data
        ttk.Label(body, text="Data").pack(anchor=tk.W)
        self.date_entry = MaskedEntry(
            body, format_date, textvariable=self.var_data, width=12
        )
        self.date_entry.pack(fill=tk.X, pady=4)

        # Tipo (Débito/Crédito)
        ttk.Label(body, text="Tipo").pack(anchor=tk.W)
        ttk.Combobox(
            body,
            state="readonly",
            textvariable=self.var_tipo,
            values=("Débito", "Crédito"),
        ).pack(fill=tk.X, pady=4)

        # Valor
        ttk.Label(body, text="Valor").pack(anchor=tk.W)
        self.valor_entry = MaskedEntry(
            body, format_money, textvariable=self.var_valor, width=20
        )
        self.valor_entry.pack(fill=tk.X, pady=4)

        # Descrição
        self._add_labeled_entry(body, "Descrição", self.var_descricao)

        # Botões
        self._btns(self._on_ok, self.destroy)

        # Eventos para tentar preencher o nome do locador automático
        self.cmb_imovel.bind("<<ComboboxSelected>>", self._auto_nome)
        self.cmb_parte.bind("<<ComboboxSelected>>", self._auto_nome)

    def _get_selected_imovel(self) -> Optional[Imovel]:
        txt = self.var_imovel.get().strip()
        if not txt:
            return None
        try:
            imovel_id = int(txt.split("-", 1)[0].strip())
        except Exception:
            return None
        return self.store.get_imovel(imovel_id)

    def _auto_nome(self, _event=None):
        imovel = self._get_selected_imovel()
        if not imovel:
            return

        parte = self.var_parte.get()
        nome = ""

        if parte == "Locador":
            # pega o proprietário do imóvel
            pid = getattr(imovel, "proprietario_id", None)
            if pid:
                locador = self.store.get_locador(pid)
                if locador:
                    nome = locador.nome

        elif parte == "Locatário":
            # pega o locatário do imóvel
            tid = getattr(imovel, "locatario_id", None)
            if tid:
                locatario = self.store.get_locatario(tid)
                if locatario:
                    nome = locatario.nome

        if nome:
            self.var_pessoa_nome.set(nome)

    def _on_ok(self):
        # Validações básicas
        if not self.var_imovel.get().strip():
            messagebox.showwarning("Validação", "Selecione um imóvel.")
            return

        if not self.var_data.get().strip():
            messagebox.showwarning("Validação", "Informe a data da ocorrência.")
            return

        valor = parse_money(self.var_valor.get())
        if valor == 0:
            if not messagebox.askyesno(
                "Confirmação",
                "O valor está 0,00. Deseja continuar mesmo assim?"
            ):
                return

        imovel = self._get_selected_imovel()
        if not imovel:
            messagebox.showwarning("Validação", "Imóvel inválido.")
            return

        pessoa_nome = self.var_pessoa_nome.get().strip()
        if not pessoa_nome:
            messagebox.showwarning("Validação", "Informe o nome da pessoa.")
            return

        self.result = Ocorrencia(
            id=self._initial_id or 0,
            imovel_id=imovel.id,
            parte=self.var_parte.get(),
            pessoa_nome=pessoa_nome,
            data=self.var_data.get().strip(),
            tipo=self.var_tipo.get(),
            valor=valor,
            descricao=self.var_descricao.get().strip(),
        )
        self.destroy()

    
# --Abas-- #

class LocadoresTab(ttk.Frame):
    def __init__(self, master, store: DataStore):
        super().__init__(master)
        self.store = store
        columns = [
            ("id", "ID", 60),
            ("nome", "Nome", 220),
            ("cpf_cnpj", "CPF/CNPJ", 140),
            ("email", "Email", 200),
            ("telefone", "Telefone", 120),
        ]
        self.list = ListWithActions(self, columns, self._new, self._edit, self._delete)
        self.list.pack(fill=tk.BOTH, expand=True)
        self.list.tree.bind(
            "<Double-1>",
            lambda e: self._view_details(self.list._selected_id())
        )
        self.refresh()

    def refresh(self):
        rows = [asdict(l) for l in self.store.list_locadores()]

        for r in rows:
            for k in list(r.keys()):
                if k not in self.list.tree["columns"]:
                    r.pop(k)
        self.list.set_rows(rows)

    def _new(self):
        form = LocadorForm(self)
        self.wait_window(form)
        if form.result:
            self.store.create_locador(form.result)
            self.refresh()

    def _edit(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Editar", "Seleciona um locador na lista.")
            return
        current = next((l for l in self.store.list_locadores() if l.id == obj_id), None)
        form = LocadorForm(self, current)
        self.wait_window(form)
        if form.result:
            self.store.update_locador(obj_id, form.result)
            self.refresh()

    def _delete(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Excluir", "Selecione um locador na lista.")
            return
        if messagebox.askyesno("Confirmação", "Excluir este locador ? Esta ação não pode ser desfeito."):
            self.store.delete_locador(obj_id)
            self.refresh()

    def _view_details(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Detalhes", "Selecione um locador na lista.")
            return

        loc = next((l for l in self.store.list_locadores() if l.id == obj_id), None)
        if not loc:
            messagebox.showwarning("Detalhes", "Locador não encontrado.")
            return

        win = tk.Toplevel(self)
        win.title("Detalhes do locador")
        win.geometry("500x500")
        win.transient(self)
        win.grab_set()

        scroll = ScrollableFrame(win, height=450, padding=(20, 15))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        def add(l, v):
            ttk.Label(body, text=l, font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(8, 0))
            ttk.Label(body, text=v or "-", font=("Segoe UI", 10)).pack(anchor=tk.W)

        add("ID", str(loc.id))
        add("Nome", loc.nome)
        add("CPF/CNPJ", loc.cpf_cnpj)
        add("E-mail", loc.email)
        add("Telefone", loc.telefone)
        add("Endereço", loc.endereco)
        add("Cidade", loc.cidade)
        add("Estado", loc.estado)
        add("CEP", loc.cep)
        add("Observações", loc.observacoes)
    
        # botão de fechar embaixo
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(btn_frame, text="Fechar", command=win.destroy).pack(pady=4)

class LocatariosTab(ttk.Frame):
    def __init__(self, master, store: DataStore):
        super().__init__(master)
        self.store = store
        columns = [
            ("id", "ID", 60),
            ("nome", "Nome", 220),
            ("cpf_cnpj", "CPF/CNPJ", 140),
            ("email", "E-mail", 200),
            ("telefone", "Telefone", 120),
        ]
        self.list = ListWithActions(self, columns, self._new, self._edit, self._delete)
        self.list.pack(fill=tk.BOTH, expand=True)
        self.list.tree.bind(
            "<Double-1>",
            lambda e: self._view_details(self.list._selected_id())
        )
        self.refresh()

    def refresh(self):
        rows = [asdict(l) for l in self.store.list_locatarios()]
        for r in rows:
            for k in list(r.keys()):
                if k not in self.list.tree["columns"]:
                    r.pop(k)
        self.list.set_rows(rows)

    def _new(self):
        form = LocatarioForm(self)
        self.wait_window(form)
        if form.result:
            self.store.create_locatario(form.result)
            self.refresh()

    def _edit(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Editar", "Selecione um locatário na lista.")
            return
        current = next((l for l in self.store.list_locatarios() if l.id == obj_id), None)
        form = LocatarioForm(self, current)
        self.wait_window(form)
        if form.result:
            self.store.update_locatario(obj_id, form.result)
            self.refresh()

    def _delete(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Excluir", "Selecione um locatário na lista.")
            return
        if messagebox.askyesno("Confimação", "Excluir esse locatário ? Esta ação não pode ser desfeita."):
            self.store.delete_locatario(obj_id)
            self.refresh()

    def _view_details(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Detalhes", "Selecione um locatário na lista.")
            return

        loc = next((l for l in self.store.list_locatarios() if l.id == obj_id), None)
        if not loc:
            messagebox.showwarning("Detalhes", "Locatário não encontrado.")
            return

        win = tk.Toplevel(self)
        win.title("Detalhes do locatário")
        win.geometry("500x600")
        win.transient(self)
        win.grab_set()

        scroll = ScrollableFrame(win, height=500, padding=(20, 15))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        def add(l, v):
            ttk.Label(body, text=l, font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(8, 0))
            ttk.Label(body, text=v or "-", font=("Segoe UI", 10)).pack(anchor=tk.W)

        add("ID", str(loc.id))
        add("Nome", loc.nome)
        add("CPF/CNPJ", loc.cpf_cnpj)
        add("E-mail", loc.email)
        add("Telefone", loc.telefone)
        add("Observações", loc.observacoes)

        add("Garantia - Tipo", getattr(loc, "garantia_tipo", ""))

        # Caução
        add("Caução - Descrição", getattr(loc, "caucao_descricao", ""))
        add("Caução - Valor", money_str(float(getattr(loc, "caucao_valor", 0.0) or 0.0)) if getattr(loc, "caucao_valor", 0.0) else "")
        add("Caução - Data", getattr(loc, "caucao_data", ""))

        # Fiador
        add("Fiador - Nome", getattr(loc, "fiador_nome", ""))
        add("Fiador - CPF", getattr(loc, "fiador_cpf", ""))
        add("Fiador - Telefone", getattr(loc, "fiador_tel", ""))
        add("Fiador - E-mail", getattr(loc, "fiador_email", ""))

        # Seguro fiança
        add("Seguro fiança - Instituição", getattr(loc, "seguro_instituicao", ""))
        add("Seguro fiança - Valor", money_str(float(getattr(loc, "seguro_valor", 0.0) or 0.0)) if getattr(loc, "seguro_valor", 0.0) else "")
        add("Seguro fiança - Data", getattr(loc, "seguro_data", ""))

        # Observação geral da garantia
        add("Garantia - Observações", getattr(loc, "garantia_obs", ""))

        # botão de fechar embaixo
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(btn_frame, text="Fechar", command=win.destroy).pack(pady=4)

class ImoveisTab(ttk.Frame):
    def __init__(self, master, store: DataStore):
        super().__init__(master)
        self.store = store
        columns = [
            ("id", "ID", 60),
            ("descricao", "Descrição", 260),
            ("endereco", "Endereço", 220),
            ("proprietario_id", "Proprietário (ID)", 120),
            ("cidade", "Cidade", 120),
        ]
        self.list = ListWithActions(self, columns, self._new, self._edit, self._delete)
        self.list.pack(fill=tk.BOTH, expand=True)
        self.list.tree.bind("<Double-1>", self._on_double_click)
        self.refresh()

        # Botões de recibo / relatórios
        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, pady=4)

        ttk.Button(
            btns,
            text="Recibo do imóvel selecionado",
            command=self._abrir_recibo_imovel,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btns,
            text="Recibos do proprietário (dos imóveis dele)",
            command=self._abrir_recibos_proprietario,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btns,
            text="Financeiro do mês (todos os imóveis)",
            command=self._financeiro_mes,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btns,
            text="Balancete do proprietário",
            command=self._balancete_proprietario,
            ).pack(side=tk.LEFT, padx=4)


    def _balancete_proprietario(self):
        imv_id = self.list.get_selected_id()
        if not imv_id:
            messagebox.showinfo("Balancete", "Selecione um imóvel para pegar o proprietário.")
            return

        imv = self.store.get_imovel(imv_id)
        if not imv or not imv.proprietario_id:
            messagebox.showinfo("Balancete", "Este imóvel não tem proprietário vinculado.")
            return

        win = PeriodoReciboWindow(self)
        self.wait_window(win)
        if not win.periodo:
            return

        mes, ano = win.periodo
        BalanceteProprietarioWindow(self, self.store, imv.proprietario_id, mes, ano)

    def _financeiro_mes(self):
        win = PeriodoReciboWindow(self)
        self.wait_window(win)
        if not win.periodo:
            return
        mes, ano = win.periodo
        FinanceiroMesWindow(self, self.store, mes, ano)

    def _abrir_recibo_imovel(self):
        imv_id = self.list.get_selected_id()
        if not imv_id:
            messagebox.showinfo("Recibo", "Selecione um imóvel.")
            return

        win = PeriodoReciboWindow(self)
        self.wait_window(win)
        if not win.periodo:
            return

        mes, ano = win.periodo

        resumo = self.store.calcular_resumo_imovel_periodo(imv_id, mes, ano, parte="locatario")

        resumo["mes"] = mes
        resumo["ano"] = ano

        ReciboImovelWindow(self, resumo, self.store)

    def _abrir_recibos_proprietario(self):
        imv_id = self.list.get_selected_id()
        if not imv_id:
            messagebox.showinfo("Recibos", "Selecione um imóvel (para pegar o proprietário).")
            return

        imv_base = next((i for i in self.store.list_imoveis() if i.id == imv_id), None)
        if not imv_base or not getattr(imv_base, "proprietario_id", None):
            messagebox.showerror("Recibos", "O imóvel selecionado não tem proprietário vinculado.")
            return

        proprietario_id = imv_base.proprietario_id

        # escolhe período
        win = PeriodoReciboWindow(self)
        self.wait_window(win)
        if not win.periodo:
            return

        mes, ano = win.periodo

        # monta resumos de TODOS os imóveis desse proprietário
        resumos = calcular_resumos_proprietario_periodo(self.store, proprietario_id, mes, ano)

        if not resumos:
            messagebox.showinfo("Recibos", "Esse proprietário não possui imóveis cadastrados.")
            return

        prop = self.store.get_locador(proprietario_id)
        prop_nome = prop.nome if prop else f"ID {proprietario_id}"

        RecibosProprietarioWindow(self, resumos, self.store, prop_nome, mes, ano)

    def refresh(self):
        rows = [asdict(i) for i in self.store.list_imoveis()]
        for r in rows:
            for k in list(r.keys()):
                if k not in self.list.tree["columns"]:
                    r.pop(k)
        self.list.set_rows(rows)
    
    def _new(self):
        form = ImovelForm(self, owners=self.store.list_locadores())
        self.wait_window(form)
        if form.result:
            self.store.create_imovel(form.result)
            self.refresh()

    def _edit(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Editar", "Selecione um imóvel na lista.")
            return
        current = next((i for i in self.store.list_imoveis() if i.id == obj_id), None)
        form = ImovelForm(self, current, owners=self.store.list_locadores())
        self.wait_window(form)
        if form.result:
            self.store.update_imovel(obj_id, form.result)
            self.refresh()
    
    def _delete(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Excluir", "Selecione um imóvel na lista.")
            return
        if messagebox.askyesno("Confirmação", "Excluir esse imóvel? Esta ação não pode ser desfeita."):
            self.store.delete_imovel(obj_id)
            self.refresh()

    def _on_double_click(self, event):
        sel = self.list.tree.selection()
        if not sel:
            return
        try:
            # primeira coluna da linha é o ID
            obj_id = int(self.list.tree.item(sel[0], "values")[0])
        except Exception:
            return

        self._view_details(obj_id)

    def _view_details(self, obj_id: int):
        if not obj_id:
            return

        imovel = next((i for i in self.store.list_imoveis() if i.id == obj_id), None)
        if not imovel:
            messagebox.showwarning("Aviso", "Imóvel não encontrado.")
            return

        # --- Janela de detalhes ---
        win = tk.Toplevel(self)
        win.title("Detalhes - Imóvel")
        win.geometry("520x500")  # pode ajustar se quiser
        win.transient(self.winfo_toplevel())
        win.grab_set()

        # Usa o ScrollableFrame pra poder rolar
        scroll = ScrollableFrame(win, height=450, padding=(20, 15))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        # Fontes maiores
        label_font = ("Segoe UI", 11, "bold")
        value_font = ("Segoe UI", 11)

        def add_row(label: str, value: str = ""):
            ttk.Label(body, text=label, font=label_font).pack(anchor=tk.W, pady=(6, 0))
            ttk.Label(body, text=value or "-", font=value_font).pack(anchor=tk.W)

        # Proprietário
        owner_txt = "-"
        if getattr(imovel, "proprietario_id", None):
            owner = self.store.get_locador(imovel.proprietario_id)
            if owner:
                owner_txt = f"{owner.nome} (ID {owner.id})"

        # Locatário
        tenant_txt = "-"
        if getattr(imovel, "locatario_id", None):
            loc = self.store.get_locatario(imovel.locatario_id)
            if loc:
                tenant_txt = f"{loc.nome} (ID {loc.id})"

        # Valor do aluguel (formatado em R$)
        raw_val = getattr(imovel, "valor_aluguel", 0.0) or 0.0
        if isinstance(raw_val, (int, float)):
            base_val = float(raw_val)
        else:
            base_val = parse_money(str(raw_val))

        centavos = int(round(base_val * 100))
        valor_fmt = format_money(str(centavos)) if centavos else ""

        # Monta os campos
        add_row("ID", str(imovel.id))
        add_row("Descrição", imovel.descricao)
        add_row("Endereço", imovel.endereco)
        add_row("Bairro", getattr(imovel, "bairro", ""))
        add_row("Cidade", imovel.cidade)
        add_row("Estado", imovel.estado)
        add_row("CEP", imovel.cep)

        add_row("Proprietário", owner_txt)
        add_row("Locatário", tenant_txt)

        add_row("Tipo de reajuste", getattr(imovel, "tipo_reajuste", ""))
        add_row("Valor do aluguel", valor_fmt)
        add_row("Data de início", getattr(imovel, "data_inicio", ""))
        add_row("Mês de reajuste", getattr(imovel, "mes_reajuste", ""))
        add_row("Dia de vencimento", str(getattr(imovel, "dia_vencimento", "")))

        add_row("Observações", getattr(imovel, "observacoes", ""))

        # botão de fechar embaixo
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(btn_frame, text="Fechar", command=win.destroy).pack(pady=4)

class OcorrenciasTab(ttk.Frame):
    def __init__(self, master, store: DataStore):
        super().__init__(master)
        self.store = store

        columns = [
            ("id",          "ID",           60),
            ("imovel",      "Imóvel",      250),
            ("parte",       "Parte",       90),
            ("pessoa_nome", "Nome",        200),
            ("data",        "Data",        100),
            ("tipo",        "Tipo",        80),
            ("valor",       "Valor",       100),
        ]

        self.list = ListWithActions(
            self,
            columns,
            self._new,
            self._edit,
            self._delete,
        )
        self.list.pack(fill=tk.BOTH, expand=True)
        self.list.tree.bind(
            "<Double-1>",
            lambda e: self._view_details(self.list._selected_id())
        )
        self.refresh()

    def refresh(self):
        rows = []
        for o in self.store.list_ocorrencias():
            # Imóvel formatado
            imovel = self.store.get_imovel(o.imovel_id)
            if imovel:
                imovel_txt = f"{imovel.id} - {imovel.descricao}"
            else:
                imovel_txt = str(o.imovel_id)

            # Valor formatado
            try:
                base_val = float(getattr(o, "valor", 0.0) or 0.0)
            except (ValueError, TypeError):
                base_val = 0.0
            centavos = int(round(base_val * 100))
            valor_fmt = format_money(str(centavos)) if centavos else ""

            rows.append({
                "id":          o.id,
                "imovel":      imovel_txt,
                "parte":       o.parte,
                "pessoa_nome": o.pessoa_nome,
                "data":        o.data,
                "tipo":        o.tipo,
                "valor":       valor_fmt,
            })

        self.list.set_rows(rows)

    def _new(self):
        form = OcorrenciaForm(self, self.store)
        self.wait_window(form)
        if form.result:
            self.store.add_ocorrencia(form.result)
            self.refresh()

    def _edit(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Editar", "Selecione uma ocorrência na lista.")
            return

        current = next(
            (o for o in self.store.list_ocorrencias() if o.id == obj_id),
            None
        )
        if not current:
            messagebox.showwarning("Aviso", "Ocorrência não encontrada.")
            return

        form = OcorrenciaForm(self, self.store, current)
        self.wait_window(form)
        if form.result:
            self.store.update_ocorrencia(form.result)
            self.refresh()

    def _delete(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Excluir", "Selecione uma ocorrência na lista.")
            return

        if not messagebox.askyesno(
            "Confirmação",
            "Excluir esta ocorrência? Esta ação não pode ser desfeita."
        ):
            return

        self.store.delete_ocorrencia(obj_id)
        self.refresh()

    def _view_details(self, obj_id: Optional[int]):
        if not obj_id:
            messagebox.showinfo("Detalhes", "Selecione uma ocorrência na lista.")
            return

        oc = next((o for o in self.store.list_ocorrencias() if o.id == obj_id), None)
        if not oc:
            messagebox.showwarning("Detalhes", "Ocorrência não encontrada.")
            return

        imovel = self.store.get_imovel(oc.imovel_id) if hasattr(self.store, "get_imovel") else None
        nome_imovel = f"{imovel.id} - {imovel.descricao}" if imovel else f"ID {oc.imovel_id}"

        # formata valor em dinheiro
        valor_txt = format_money(str(int(round(oc.valor * 100)))) if hasattr(oc, "valor") else ""

        win = tk.Toplevel(self)
        win.title("Detalhes da ocorrência")
        win.geometry("520x500")
        win.transient(self)
        win.grab_set()

        scroll = ScrollableFrame(win, height=450, padding=(20, 15))
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        def add(l, v):
            ttk.Label(body, text=l, font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(8, 0))
            ttk.Label(body, text=v or "-", font=("Segoe UI", 10)).pack(anchor=tk.W)

        add("ID", str(oc.id))
        add("Imóvel", nome_imovel)
        add("Parte", oc.parte if hasattr(oc, "parte") else getattr(oc, "alvo", ""))
        add("Nome", oc.pessoa_nome)
        add("Data", oc.data)
        add("Tipo", oc.tipo)
        add("Valor", valor_txt)
        add("Descrição", getattr(oc, "descricao", ""))

        # botão de fechar embaixo
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(btn_frame, text="Fechar", command=win.destroy).pack(pady=4)

class DetailWindow(tk.Toplevel):
    def __init__(self, master, title: str, data: dict):
        super().__init__(master)
        self.title(f"Detalhes - {title}")
        self.geometry("420x480")
        self.resizable(False, False)

        frame = tk.Frame(self, padx=15, pady=15)
        frame.pack(fill=tk.BOTH, expand=True)

        for campo, valor in data.items():
            ttk.Label(frame, text=campo, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            ttk.Label(frame, text=str(valor)).pack(anchor="w", pady=(0, 8))

        ttk.Button(frame, text="Fechar", command=self.destroy).pack(pady=10)

    # -- Ações -- #

    def _new(self):
        form = OcorrenciaForm(self, self.store)
        self.wait_window(form)
        if getattr(form, "result", None):
            self.store.add_ocorrencia(form.result)
            self.refresh()

    def _edit(self, occ_id: Optional[int]):
        if not occ_id:
            return
        occ = self.store.get_ocorrencia(occ_id)
        if not occ:
            return
        
        form = OcorrenciaForm(self, self.store, initial=occ)
        self.wait_window(form)
        if getattr(form, "result", None):
            self.store.update_ocorrencia(form.result)
            self.refresh()

    def _delete(self, occ_id: Optional[int]):
        if not occ_id:
            return
        if not messagebox.askyesno(
            "Confirmação",
            "Excluir a ocorrência selecionada?"
        ):
            return
        self.store.delete_ocorrencia(occ_id)
        self.refresh()

class ReciboImovelWindow(tk.Toplevel):
    def __init__(self, master, resumo: dict, store: "DataStore"):
        super().__init__(master)
        self.title("Boleta do imóvel")
        self.resumo = resumo
        self.store = store
        self._ultimo_pdf = None

        # --------------------------------------
        #   Layout geral: conteúdo + coluna ações
        # --------------------------------------
        self.geometry("750x500")
        self.minsize(600, 400)

        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # esquerda: conteúdo rolável
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ScrollableFrame(left, padding=(15, 15), height=430)
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        # direita: coluna de ações
        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        imv = resumo["imovel"]

        # tenta descobrir e-mail do locatário para uso no mailto
        self._email_dest = ""
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            if loc and getattr(loc, "email", ""):
                self._email_dest = loc.email

        # --- NOME DO LOCATÁRIO (para usar no PDF) ---
        self.resumo["locatario_nome"] = ""
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            if loc:
                self.resumo["locatario_nome"] = loc.nome

        # --- NOME DO PROPRIETÁRIO (para usar no PDF) ---
        self.resumo["proprietario_nome"] = ""
        if getattr(imv, "proprietario_id", None):
            prop = store.get_locador(imv.proprietario_id)
            if prop:
                self.resumo["proprietario_nome"] = prop.nome

        # --------------------------------------
        #   Conteúdo do recibo (lado esquerdo)
        # --------------------------------------
        font_titulo = ("Segoe UI", 13, "bold")
        font_secao  = ("Segoe UI", 11, "bold")
        font_texto  = ("Segoe UI", 11)

        ttk.Label(
            body,
            text="Boleta     de aluguel",
            font=font_titulo
        ).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(body, text=f"ID: {imv.id}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Descrição: {imv.descricao}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Endereço: {imv.endereco}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(
            body,
            text=f"Cidade/UF: {imv.cidade} - {imv.estado}",
            font=font_texto
        ).pack(anchor=tk.W)
        ttk.Label(
            body,
            text=f"Valor do aluguel: {money_str(resumo['valor_aluguel'])}",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor=tk.W, pady=(6, 12))

        # Ocorrências
        ttk.Label(
            body,
            text="Ocorrências vinculadas:",
            font=font_secao
        ).pack(anchor=tk.W)

        if not resumo["ocorrencias"]:
            ttk.Label(
                body,
                text="Não há ocorrências lançadas.",
                font=font_texto
            ).pack(anchor=tk.W, pady=(2, 10))
        else:
            for oc in resumo["ocorrencias"]:
                txt = (
                    f"{oc.data}  - {oc.tipo}  - "
                    f"{money_str(oc.valor)}  - {oc.descricao}"
                )
                ttk.Label(body, text=txt, font=font_texto).pack(anchor=tk.W)

        ttk.Label(body, text="", font=font_texto).pack(anchor=tk.W, pady=4)

        ttk.Label(
            body,
            text=f"Total de débitos: {money_str(resumo['total_debitos'])}",
            font=font_texto
        ).pack(anchor=tk.W)
        ttk.Label(
            body,
            text=f"Total de créditos: {money_str(resumo['total_creditos'])}",
            font=font_texto
        ).pack(anchor=tk.W)

        ttk.Label(
            body,
            text=f"TOTAL A PAGAR: {money_str(resumo['total_a_pagar'])}",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor=tk.W, pady=(10, 4))

        ttk.Label(body, text="", font=font_texto).pack(anchor=tk.W, pady=6)
        ttk.Label(body, text="Observação na boleta (opcional):", font=font_secao).pack(anchor=tk.W)

        self._obs_var = tk.StringVar()
        ttk.Entry(body, textvariable=self._obs_var).pack(anchor=tk.W, fill=tk.X, pady=(4, 0))

        # --------------------------------------
        #   Coluna de ações (lado direito)
        # --------------------------------------
        ttk.Label(
            right,
            text="Ações",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor=tk.N, pady=(0, 8))

        ttk.Button(
            right,
            text="Salvar em PDF...",
            command=self._salvar_pdf,
            width=20
        ).pack(fill=tk.X, pady=3)

        ttk.Button(
            right,
            text="Imprimir",
            command=self._imprimir_pdf,
            width=20
        ).pack(fill=tk.X, pady=3)

        ttk.Button(
            right,
            text="Enviar por e-mail",
            command=self._email_texto,
            width=20
        ).pack(fill=tk.X, pady=3)

        ttk.Button(
            right,
            text="Enviar por WhatsApp",
            command=self._enviar_whatsapp,
            width=20
        ).pack(fill=tk.X, pady=3)

        ttk.Button(
            right,
            text="Fechar",
            command=self.destroy,
            width=20
        ).pack(fill=tk.X, pady=(20, 0))

    # --------- ações ----------

    def _salvar_pdf(self):
        from tkinter import messagebox
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar boleta em PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        try:
            self.resumo["observacao_boleta"] = (getattr(self, "_obs_var", None).get() if hasattr(self, "_obs_var") else "").strip()
            gerar_pdf_recibo_imovel(path, self.resumo)
            self._ultimo_pdf = path
            messagebox.showinfo("Recibo", "PDF gerado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao gerar PDF:\n{e}")

    def _imprimir_pdf(self):
        self.resumo["observacao_boleta"] = (getattr(self, "_obs_var", None).get() if hasattr(self, "_obs_var") else "").strip()
        caminho = imprimir_recibo(self.resumo)
        if caminho:
            self._ultimo_pdf = caminho

    def _email_texto(self):
        imv = self.resumo["imovel"]
        assunto = f"Boleta de aluguel - imóvel {imv.descricao}"
        corpo = (
            f"Recibo de aluguel do imóvel {imv.descricao}.\n"
            f"Total a pagar: {money_str(self.resumo['total_a_pagar'])}\n\n"
        )

        import urllib.parse

        # DESTINATÁRIO: e-mail do locatário, se existir
        to = self._email_dest or ""
        query = "subject=" + urllib.parse.quote(assunto) + \
                "&body=" + urllib.parse.quote(corpo)

        url = f"mailto:{urllib.parse.quote(to)}?{query}"
        webbrowser.open(url)

    def _enviar_whatsapp(self):
        import urllib.parse

        self.resumo["observacao_boleta"] = (
        (getattr(self, "_obs_var", None).get() if hasattr(self, "_obs_var") else "").strip()
        )

        # 1) gerar PDF
        caminho_pdf = caminho_pdf_recibo_organizado(self.resumo)
        gerar_pdf_recibo_imovel(caminho_pdf, self.resumo)
        self._ultimo_pdf = caminho_pdf

        # 2) pegar imóvel do recibo
        imv = self.resumo.get("imovel")
        if not imv:
            messagebox.showerror("WhatsApp", "Imóvel não encontrado no recibo.")
            return

        # 3) pegar locatário vinculado ao imóvel
        locatario_id = getattr(imv, "locatario_id", None)
        if not locatario_id:
            messagebox.showerror("WhatsApp", "Locatário não vinculado ao imóvel.")
            return

        loc = self.store.get_locatario(locatario_id)
        if not loc:
            messagebox.showerror("WhatsApp", "Cadastro do locatário não encontrado.")
            return

        telefone = getattr(loc, "telefone", "") or ""
        numero = re.sub(r"\D", "", telefone)

        # 4) normalizar para BR (se vier só 11 dígitos)
        if len(numero) == 11:
            numero = "55" + numero

        if len(numero) < 12:
            messagebox.showerror("WhatsApp", "Telefone do locatário inválido ou incompleto.")
            return

        # 5) montar mensagem
        mes = self.resumo.get("mes")
        ano = self.resumo.get("ano")
        periodo = f"{int(mes):02d}/{ano}" if mes and ano else "--/----"

        total = self.resumo.get("total_a_pagar", 0)

        msg = (
            f"Olá {loc.nome}, tudo bem?\n\n"
            f"Segue a boleta de aluguel.\n"
            f"Imóvel: {imv.descricao}\n"
            f"Período: {periodo}\n"
            f"Total: {money_str(total)}\n\n"
            f"Vou anexar o PDF aqui na conversa."
        )

        url = f"https://wa.me/{numero}?text=" + urllib.parse.quote(msg)
        webbrowser.open(url)

        # 7) abrir a pasta do PDF para anexar mais rápido
        try:
            os.startfile(os.path.dirname(caminho_pdf))
        except Exception:
            pass

        messagebox.showinfo(
            "WhatsApp",
            "WhatsApp aberto com a mensagem pronta.\n\n"
            "O PDF foi gerado e a pasta foi aberta.\n"
        )

class RecibosProprietarioWindow(tk.Toplevel):
    def __init__(self, master, resumos: list[dict], store: "DataStore", proprietario_nome: str, mes: int, ano: int):
        super().__init__(master)
        self.title(f"Recibos do proprietário - {proprietario_nome}")
        self.geometry("900x560")
        self.minsize(750, 450)

        top = ttk.Frame(self, padding=(10, 10))
        top.pack(fill=tk.X)

        ttk.Label(
            top,
            text=f"Proprietário: {proprietario_nome} | Período: {int(mes):02d}/{ano}",
            font=("Segoe UI", 11, "bold")
        ).pack(side=tk.LEFT)

        ttk.Button(
            top,
            text="Salvar TODOS em PDF (1 por imóvel)...",
            command=lambda: self._salvar_todos(resumos)
        ).pack(side=tk.RIGHT)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        for resumo in resumos:
            imv = resumo["imovel"]
            titulo = f"ID {imv.id} - {imv.descricao}"
            frame = ReciboImovelPanel(self.nb, resumo, store)
            self.nb.add(frame, text=titulo)

    def _salvar_todos(self, resumos: list[dict]):
        pasta = filedialog.askdirectory(parent=self, title="Escolha a pasta para salvar os PDFs")
        if not pasta:
            return

        erros = 0
        for resumo in resumos:
            try:
                nome = os.path.basename(caminho_pdf_recibo_organizado(resumo))
                path = os.path.join(pasta, nome)
                gerar_pdf_recibo_imovel(path, resumo)
            except Exception:
                erros += 1

        if erros == 0:
            messagebox.showinfo("PDF", "Todos os PDFs foram gerados com sucesso.")
        else:
            messagebox.showwarning("PDF", f"Concluído, mas {erros} PDF(s) falharam. Verifique os imóveis/dados.")


class ReciboImovelPanel(ttk.Frame):
    def __init__(self, master, resumo: dict, store: "DataStore"):
        super().__init__(master)
        self.resumo = resumo
        self.store = store
        self._ultimo_pdf = None

        # tenta descobrir e-mail do locatário para uso no mailto
        imv = resumo["imovel"]
        self._email_dest = ""
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            if loc and getattr(loc, "email", ""):
                self._email_dest = loc.email

        # nome do locatário e do proprietário pro PDF
        self.resumo["locatario_nome"] = ""
        if getattr(imv, "locatario_id", None):
            loc = store.get_locatario(imv.locatario_id)
            if loc:
                self.resumo["locatario_nome"] = loc.nome

        self.resumo["proprietario_nome"] = ""
        if getattr(imv, "proprietario_id", None):
            prop = store.get_locador(imv.proprietario_id)
            if prop:
                self.resumo["proprietario_nome"] = prop.nome

        # Layout: conteúdo + ações 
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ScrollableFrame(left, padding=(15, 15), height=430)
        scroll.pack(fill=tk.BOTH, expand=True)
        body = scroll.body

        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        font_titulo = ("Segoe UI", 13, "bold")
        font_secao  = ("Segoe UI", 11, "bold")
        font_texto  = ("Segoe UI", 11)

        ttk.Label(body, text="Boleta de aluguel", font=font_titulo).pack(anchor=tk.W, pady=(0, 10))

        mes = self.resumo.get("mes")
        ano = self.resumo.get("ano")
        periodo = f"{int(mes):02d}/{ano}" if mes and ano else "--/----"
        ttk.Label(body, text=f"Período: {periodo}", font=font_texto).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(body, text=f"ID: {imv.id}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Descrição: {imv.descricao}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Endereço: {imv.endereco}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Cidade/UF: {imv.cidade} - {imv.estado}", font=font_texto).pack(anchor=tk.W)

        ttk.Label(
            body,
            text=f"Valor do aluguel: {money_str(self.resumo['valor_aluguel'])}",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor=tk.W, pady=(6, 12))

        ttk.Label(body, text="Ocorrências vinculadas:", font=font_secao).pack(anchor=tk.W, pady=(0, 6))

        ocorrencias = self.resumo.get("ocorrencias", [])
        if not ocorrencias:
            ttk.Label(body, text="Não há ocorrências no período.", font=font_texto).pack(anchor=tk.W)
        else:
            for oc in ocorrencias:
                txt = f"{oc.data} - {oc.tipo} - {money_str(float(oc.valor))} - {oc.descricao}"
                ttk.Label(body, text=txt, font=font_texto).pack(anchor=tk.W, pady=1)

        ttk.Label(body, text="", font=font_texto).pack(anchor=tk.W, pady=8)
        ttk.Label(body, text=f"Total de débitos: {money_str(self.resumo['total_debitos'])}", font=font_texto).pack(anchor=tk.W)
        ttk.Label(body, text=f"Total de créditos: {money_str(self.resumo['total_creditos'])}", font=font_texto).pack(anchor=tk.W)

        ttk.Label(body, text="", font=font_texto).pack(anchor=tk.W, pady=6)
        ttk.Label(body, text=f"TOTAL A PAGAR: {money_str(self.resumo['total_a_pagar'])}", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        # AÇÕES (direita)
        ttk.Label(right, text="Ações", font=("Segoe UI", 11, "bold")).pack(pady=(0, 10))

        ttk.Button(right, text="Salvar em PDF...", command=self._salvar_pdf).pack(fill=tk.X, pady=4)
        ttk.Button(right, text="Imprimir", command=self._imprimir_pdf).pack(fill=tk.X, pady=4)
        ttk.Button(right, text="Enviar por e-mail", command=self._email_texto).pack(fill=tk.X, pady=4)
        ttk.Button(right, text="Enviar por WhatsApp", command=self._enviar_whatsapp).pack(fill=tk.X, pady=4)

    def _salvar_pdf(self):
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="Salvar Boleta em PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=os.path.basename(caminho_pdf_recibo_organizado(self.resumo)),
        )
        if not path:
            return
        try:
            gerar_pdf_recibo_imovel(path, self.resumo)
            self._ultimo_pdf = path
            messagebox.showinfo("PDF", "PDF gerado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao gerar PDF:\n{e}")

    def _imprimir_pdf(self):
        caminho = imprimir_recibo(self.resumo)
        if caminho:
            self._ultimo_pdf = caminho

    def _email_texto(self):
        imv = self.resumo["imovel"]
        assunto = f"Boleta de aluguel - imóvel {imv.descricao}"
        corpo = (
            f"Boleta de aluguel do imóvel {imv.descricao}.\n"
            f"Total a pagar: {money_str(self.resumo['total_a_pagar'])}\n\n"
            "Segue o PDF."
        )

        import urllib.parse
        to = self._email_dest or ""
        query = "subject=" + urllib.parse.quote(assunto) + "&body=" + urllib.parse.quote(corpo)
        url = f"mailto:{urllib.parse.quote(to)}?{query}"
        webbrowser.open(url)

    def _enviar_whatsapp(self):
        try:
            ReciboImovelWindow._enviar_whatsapp(self) 
        except Exception:
            messagebox.showerror("WhatsApp", "Falha ao abrir WhatsApp.")


class PeriodoReciboWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Selecionar período")
        self.resizable(False, False)

        ttk.Label(self, text="Mês:").grid(row=0, column=0, padx=10, pady=10)
        self.var_mes = tk.StringVar()
        meses = [f"{i:02d}" for i in range(1, 12+1)]
        ttk.Combobox(self, textvariable=self.var_mes, values=meses, width=5).grid(row=0, column=1)

        ttk.Label(self, text="Ano:").grid(row=1, column=0, padx=10)
        self.var_ano = tk.StringVar(value=str(datetime.now().year))
        ttk.Entry(self, textvariable=self.var_ano, width=8).grid(row=1, column=1)

        ttk.Button(self, text="OK", command=self._ok).grid(row=2, column=0, columnspan=2, pady=10)

        self.periodo = None
        self.grab_set()

    def _ok(self):
        mes = self.var_mes.get().strip()
        ano = self.var_ano.get().strip()

        if not mes or not ano:
            messagebox.showerror("Erro", "Informe mês e ano.")
            return

        try:
            mes_i = int(mes) 
            ano_i = int(ano)   
        except ValueError:
            messagebox.showerror("Erro", "Mês e ano precisam ser números.")
            return

        if not (1 <= mes_i <= 12):
            messagebox.showerror("Erro", "Mês inválido (1 a 12).")
            return

        self.periodo = (mes_i, ano_i)
        self.destroy()

class FinanceiroMesWindow(tk.Toplevel):
    def __init__(self, master, store: "DataStore", mes: int, ano: int):
        super().__init__(master)
        self.store = store
        self.mes = mes
        self.ano = ano

        self.title(f"Financeiro {mes:02d}/{ano}")
        self.geometry("380x190")
        self.transient(master.winfo_toplevel())
        self.grab_set()

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Recibo geral / Financeiro do mês", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(frm, text=f"Período: {mes:02d}/{ano}").pack(anchor=tk.W, pady=(6, 14))

        ttk.Button(frm, text="Salvar PDF", command=self._salvar_pdf).pack(fill=tk.X, pady=6)
        ttk.Button(frm, text="Imprimir direto", command=self._imprimir_direto).pack(fill=tk.X, pady=6)

    def _salvar_pdf(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar financeiro do mês em PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"Financeiro_{self.mes:02d}-{self.ano}.pdf",
        )
        if not path:
            return
        try:
            gerar_pdf_financeiro_mes(path, self.store, self.mes, self.ano)
            messagebox.showinfo("PDF", "Financeiro gerado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao gerar o financeiro:\n{e}")

    def _imprimir_direto(self):
        imprimir_pdf_temp(
            gerar_pdf_financeiro_mes,
            f"financeiro_{self.mes:02d}-{self.ano}_tmp.pdf",
            self.store, self.mes, self.ano
        )

class BalanceteProprietarioWindow(tk.Toplevel):
    def __init__(self, master, store: "DataStore", proprietario_id: int, mes: int, ano: int):
        super().__init__(master)
        self.store = store
        self.proprietario_id = proprietario_id
        self.mes = mes
        self.ano = ano

        self.title(f"Balancete {mes:02d}/{ano}")
        self.geometry("420x190")
        self.minsize(420, 300)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        owner = self.store.get_locador(proprietario_id)
        self._owner = owner
        self._email_dest = (getattr(owner, "email", "") or "") if owner else ""

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Balancete do Proprietário", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(frm, text=f"Período: {mes:02d}/{ano}").pack(anchor=tk.W, pady=(6, 2))
        ttk.Label(frm, text=f"Proprietário: {owner.nome if owner else proprietario_id}").pack(anchor=tk.W, pady=(0, 12))

        ttk.Button(frm, text="Salvar PDF", command=self._salvar).pack(fill=tk.X, pady=6)
        ttk.Button(frm, text="Imprimir direto", command=self._imprimir).pack(fill=tk.X, pady=6)
        ttk.Button(frm, text="Enviar por e-mail", command=self._email_texto).pack(fill=tk.X, pady=6)
        ttk.Button(frm, text="Enviar por WhatsApp", command=self._enviar_whatsapp).pack(fill=tk.X, pady=6)


    def _salvar(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar balancete em PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"Balancete_{self.mes:02d}-{self.ano}_Prop{self.proprietario_id}.pdf",
        )
        if not path:
            return
        try:
            gerar_pdf_balancete_proprietario(path, self.store, self.proprietario_id, self.mes, self.ano)
            messagebox.showinfo("PDF", "Balancete gerado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao gerar balancete:\n{e}")

    def _imprimir(self):
        imprimir_pdf_temp(
            gerar_pdf_balancete_proprietario,
            f"balancete_{self.mes:02d}-{self.ano}_prop{self.proprietario_id}_tmp.pdf",
            self.store, self.proprietario_id, self.mes, self.ano
        )

    def _email_texto(self):
        import urllib.parse
        import webbrowser
        owner = getattr(self, "_owner", None)
        nome = (owner.nome if owner else f"ID {self.proprietario_id}")
        assunto = f"Balancete - {self.mes:02d}/{self.ano} - {nome}"
        corpo = (
            f"Olá, {nome}!\n\n"
            f"Segue o balancete do período {self.mes:02d}/{self.ano}.\n"
            "O PDF será anexado na mensagem.\n"
        )

        to = self._email_dest or ""
        query = "subject=" + urllib.parse.quote(assunto) + "&body=" + urllib.parse.quote(corpo)
        url = f"mailto:{urllib.parse.quote(to)}?{query}"
        webbrowser.open(url)

    def _enviar_whatsapp(self):
        import os
        import urllib.parse
        import webbrowser
        import re

        owner = getattr(self, "_owner", None)
        if not owner:
            messagebox.showerror("WhatsApp", "Proprietário não encontrado.")
            return

        telefone = getattr(owner, "telefone", "") or ""
        numero = re.sub(r"\D", "", telefone)

        # normalizar para BR
        if len(numero) == 11:
            numero = "55" + numero

        if len(numero) < 12:
            messagebox.showerror("WhatsApp", "Telefone do proprietário inválido ou incompleto.")
            return

        # ===== PASTA DOCUMENTOS / IMOBMANAGER / BALANCETES =====
        base_docs = os.path.join(os.path.expanduser("~"), "Documents", "ImobManager", "Balancetes")
        os.makedirs(base_docs, exist_ok=True)

        caminho_pdf = os.path.join(
            base_docs,
            f"Balancete_{self.mes:02d}-{self.ano}_Prop{self.proprietario_id}.pdf"
        )

        # gera o PDF
        gerar_pdf_balancete_proprietario(
            caminho_pdf,
            self.store,
            self.proprietario_id,
            self.mes,
            self.ano
        )

        msg = (
            f"Olá {owner.nome}, tudo bem?\n\n"
            f"Segue o balancete do período {self.mes:02d}/{self.ano}.\n"
            f"Vou anexar o PDF aqui na conversa."
        )

        url = f"https://wa.me/{numero}?text=" + urllib.parse.quote(msg)
        webbrowser.open(url)

        # abre a pasta para facilitar anexar
        try:
            os.startfile(base_docs)
        except Exception:
            pass

        messagebox.showinfo(
            "WhatsApp",
            "WhatsApp aberto com a mensagem pronta.\n\n"
            "O PDF foi salvo na pasta Documentos > ImobManager > Balancetes."
        )


# --Aplicação Principal-- #

class ImobManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ImobManager - Gestão Imobiliária")
        self.geometry("980x560")
        self.minsize(900, 520)
        self._setup_style()

        self.store = DataStore()

        self._build_menu()
        self._build_tabs()

    def _setup_style(self):
        try:
            self.call("tk", "scaling", 1.15)
        except Exception:
            pass
        default = tkfont.nametofont("TkDefaultFont")
        default.configure(size=11)

        text = tkfont.nametofont("TkTextFont")
        text.configure(size=11)

        heading = tkfont.nametofont("TkHeadingFont")
        heading.configure(size=11, weight="bold")

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        else:
            style.theme_use("clam")

        style.configure("Treeview", font=("Segoe UI", 10), rowheight=28)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TEntry", font=("Segoe UI", 10))

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        cad_menu = tk.Menu(menubar, tearoff=0)
        cad_menu.add_command(label="Locadores", command=lambda: self._select_tab("locadores"))
        cad_menu.add_command(label="Locatários", command=lambda: self._select_tab("locatarios"))
        cad_menu.add_command(label="Imóveis", command=lambda: self._select_tab("imoveis"))
        menubar.add_cascade(label="Cadastros", menu=cad_menu)

        util_menu = tk.Menu(menubar, tearoff=0)
        util_menu.add_command(label="Sair", command=self.destroy)
        menubar.add_cascade(label="Utilitários", menu=util_menu)

        menu_sobre = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Sobre", menu=menu_sobre)
        menu_sobre.add_command(label="Sobre o sistema", command=self._sobre)


    def _build_tabs(self):
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill=tk.BOTH, expand=True)

        self.tab_locadores = LocadoresTab(self.tabs, self.store)
        self.tabs.add(self.tab_locadores, text="Locadores")

        self.tab_locatarios = LocatariosTab(self.tabs, self.store)
        self.tabs.add(self.tab_locatarios, text="Locatários")

        self.tab_imoveis = ImoveisTab(self.tabs, self.store)
        self.tabs.add(self.tab_imoveis, text="Imóveis")

        self.tab_ocorrencias = OcorrenciasTab(self.tabs, self.store)
        self.tabs.add(self.tab_ocorrencias, text="Ocorrências")

    def _select_tab(self, key: str):
        mapping = {
            "locadores": self.tab_locadores,
            "locatarios": self.tab_locatarios,
            "imoveis": self.tab_imoveis,
        }
        self.tabs.select(mapping[key])

    def _sobre(self):
        messagebox.showinfo(
            "Sobre",
            "Programa de Gestão de Imóveis v1.0\n\n"
            "Desenvolvido por:\n"
            "Rodrigo Manhães Cosenza\n"
            "Mario Francisco Cosenza"
        )

if __name__ == "__main__":
    app = ImobManagerApp()
    app.mainloop()



    
