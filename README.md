# ImobManager

Software desktop desenvolvido em Python para gestão e administração de imóveis.

O sistema foi desenvolvido sob demanda para um escritório de advocacia e gestão imobiliária, com o objetivo de organizar cadastros, controlar informações financeiras e automatizar a emissão de documentos relacionados à administração de imóveis.

## Funcionalidades

- Cadastro de locadores
- Cadastro de locatários
- Cadastro de imóveis
- Vinculação de imóveis a proprietários e locatários
- Registro de ocorrências financeiras por imóvel
- Controle de débitos e créditos
- Cálculo de valores por período
- Emissão de boletas de aluguel em PDF
- Geração de relatório financeiro mensal
- Geração de balancete por proprietário
- Impressão de documentos
- Organização dos arquivos gerados
- Armazenamento local dos dados em arquivos CSV
- Criação automática de backups dos dados

## Tecnologias utilizadas

- Python
- Tkinter
- CSV
- ReportLab
- Dataclasses
- pathlib
- datetime
- webbrowser

## Sobre o projeto

O ImobManager foi criado para facilitar a rotina administrativa de um escritório que atua com gestão de imóveis.

A aplicação permite centralizar informações de proprietários, inquilinos, imóveis, garantias locatícias, valores de aluguel, vencimentos, ocorrências, débitos, créditos e relatórios financeiros.

Além do controle cadastral, o sistema também automatiza a geração de documentos em PDF, como boletas de aluguel, relatórios financeiros e balancetes por proprietário.

## Estrutura do sistema

O sistema trabalha com quatro principais áreas de informação:

- **Locadores:** cadastro dos proprietários dos imóveis.
- **Locatários:** cadastro dos inquilinos, incluindo dados pessoais, contato e informações de garantia.
- **Imóveis:** cadastro dos imóveis, valores de aluguel, vencimentos, reajustes e vínculos com locadores e locatários.
- **Ocorrências:** registro de lançamentos financeiros, como débitos e créditos relacionados aos imóveis.

## Objetivo do projeto

O objetivo principal foi desenvolver uma ferramenta prática, funcional e personalizada para substituir controles manuais e facilitar a administração imobiliária.

O projeto envolveu:

- Desenvolvimento de interface gráfica
- Modelagem dos dados principais do sistema
- Manipulação e persistência de dados em arquivos CSV
- Geração de documentos em PDF
- Cálculos financeiros por período
- Organização automática de arquivos
- Rotinas de impressão e backup

## Status do projeto

Projeto finalizado e entregue ao cliente.

