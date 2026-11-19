# Futuro Perto · Primeira versão executável

Aplicação web em Python/Flask + SQLite com busca, filtros, páginas de detalhes, mapa real condicionado a coordenadas verificadas, formulário de comunicação, área administrativa com login, manutenção de instituições/unidades/oportunidades e exportação CSV/JSON. Interface responsiva em português.

## Iniciar no Windows (PowerShell)

Instale Python 3.11+ e execute na pasta do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY = (py -c "import secrets; print(secrets.token_hex(32))")
$env:ADMIN_USER = 'administrador'
$env:ADMIN_PASSWORD = 'TROQUE-POR-UMA-SENHA-FORTE'
py app.py
```

Abra http://127.0.0.1:5000 e faça login em http://127.0.0.1:5000/admin/login. Na primeira inicialização com variáveis ADMIN_USER/ADMIN_PASSWORD configuradas, o usuário será criado. Se iniciar sem credenciais, a área administrativa ficará sem usuário inicial. A senha precisa ser forte e deve ser guardada com segurança. Para execuções posteriores, use as mesmas variáveis. No Windows, também existe INICIAR_WINDOWS.bat, mas ele não define credenciais.

Se a política do PowerShell impedir ativação, substitua as três primeiras linhas de instalação por `py -m venv .venv` e `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, e rode `.\.venv\Scripts\python.exe app.py`.

## Testar

1. Na página inicial, escolha uma cidade e área. Verifique os resultados.
2. Em Explorar, combine filtros e teste uma pesquisa sem resultados.
3. Abra um curso e veja o aviso demonstrativo e a situação das inscrições.
4. No Mapa, inicialmente aparece aviso de falta de coordenadas verificadas. Isso é proposital, não uma falha.
5. No painel, cadastre uma instituição, depois uma unidade com coordenadas reais verificadas, depois um curso relacionado a ela. Para publicar um curso real informe fonte oficial HTTPS e data de verificação. O mapa passa a mostrar o ponto cadastrado.
6. Envie uma comunicação e marque-a como resolvida no painel.
7. Consulte `/dados.csv` e `/dados.json`: registros de demonstração são excluídos da exportação pública.

## Limitações e pré-requisitos de produção

- Seis cursos **fictícios**, identificados; não são inscrições reais. Os nomes de cidade também não representam ofertas verificadas.
- O mapa depende da biblioteca Leaflet e dos tiles OpenStreetMap servidos externamente. Sem internet, a listagem funciona, mas o mapa pode não carregar.
- Banco SQLite é apropriado para demo/local. Para implantação multiusuário, migrar para PostgreSQL, gestão de migrações e credenciais profissionais.
- A segurança inclui senhas com hash, validação de HTTPS, CSRF em formulários, verificação de autorização na aplicação e cookies de sessão. Antes da publicação pública, habilitar HTTPS no proxy, cookie Secure, limites de tentativas de login, monitoramento, backups, políticas de retenção, revisão de headers e auditoria independente.
- Não existe ingestão automática de editais, geocodificação automática, confirmação de inscrições, envio automático de e-mail nem medição de cursos concluídos.
- Administradores podem editar oportunidades, cadastrar instituições e unidades; gestão completa de edição/exclusão de instituições e unidades e papéis de acesso ainda exige implementação.
- SEO básico via títulos e descrições; sitemap dinâmico e Open Graph ainda não foram implementados.
- Há rótulos semânticos e foco visível; conformidade WCAG 2.2 AA deve ser testada formalmente.
- Os dados públicos exportados não incluem demonstrações. É necessário revisão de privacidade/LGPD conforme hospedagem real.
- Sem validação externa de dados reais, não interpretar os indicadores como impacto social ou cobertura oficial.

## Organização

`app.py` (rotas, autenticação, regras e SQLite), `templates/` (telas), `static/style.css` (design), `requirements.txt` (dependências).

## Segunda versão — Excel + mapa por municípios

- **Importar Excel na administração**: use a aba `COLETA` da planilha `Futuro_Perto_base_coleta_v1.xlsx`. A rota `/admin/importar` aceita somente `.xlsx`, limita o tamanho da requisição e aplica proteção de sessão/CSRF. Os cursos importados ficam **Em verificação** e não são publicados automaticamente. Reimportações do mesmo nome + instituição + fonte não duplicam dados; ações editoriais existentes não são sobrescritas.
- A planilha contém dados preliminares; municípios ausentes recebem `Não informado` e modalidades incertas devem ser corrigidas manualmente **antes de publicar**. Textos como `960–1440h` permanecem como observação, não como carga horária numérica. Prazos ambíguos como `jan/2026` ficam como anotação, não viram datas artificiais.
- **Mapa municipal**: polígonos de Goiás carregados do repositório público `henriquemalvar/br-geojson` (dados derivados de malhas IBGE), usando Leaflet. As cores refletem apenas o número de cursos publicados e não demonstrativos para os filtros selecionados; não informam o total real disponível em cada município. Ao clicar em um município, abre-se o resultado da busca por município. A malha usa conexão com CDN e requer acesso à internet.
- **Marcadores pontuais** continuam limitados a unidades com coordenadas verificadas.
- A EduLivre é uma **referência externa de descoberta**, não está cadastrada automaticamente como oferta individual, e nenhuma parceria é presumida. https://www.edulivre.org.br/trilhas
- As seis oportunidades fictícias da v1 continuam identificadas como exemplos. Ao testar a publicação de dados reais, revise a fonte e o prazo de inscrição manualmente.

**Atenção**: antes de colocar o site no ar, é preciso auditar validações, acessibilidade, rate limiting, segurança de login, configuração HTTPS e o uso/CDN da malha cartográfica. A v2 é um MVP local de demonstração e gestão editorial.


## Versão 3: planilha já carregada
O banco de dados `futuro_perto.db` já vem preenchido com as oportunidades da aba `cursos` da planilha atualizada. Não é necessário importá-la. Se apagar o banco, o site o recria automaticamente a partir de `cursos_iniciais.json`. Todas as oportunidades começam no estado **Em verificação** e só aparecem ao público depois da revisão e publicação no painel administrativo. Valores geográficos incoerentes não são utilizados como marcadores. Acesse /admin/login com as credenciais definidas no PowerShell e revise os cadastros.
