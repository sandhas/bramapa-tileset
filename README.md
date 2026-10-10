# bramapa-tileset

Arte e metadados de tiles do **Bramapa**, um atlas 2.5D isométrico do Brasil em pixel art
(cliente nativo em Rust + wgpu, visual à la Roller Coaster Tycoon).

Este repositório guarda o **atlas** (`atlas.png`) e o seu **contrato** (`tiles.toml`): ids,
classes, posições no atlas, animação e regras de autotiling. Ele segue a
[spec v0 de tileset](https://github.com/sandhas/bramapa/blob/main/docs/spec/tileset.md)
(`spec_version = 0`, instável até o MVP).

> **Fase 0/1 — placeholder.** O `atlas.png` atual é gerado proceduralmente (diamantes de cor
> chapada com dithering, bandas, caixinhas, espuma nas margens da água) só para o motor e o
> pipeline terem algo consistente para renderizar. A arte de verdade entra depois, em `src-art/`.

## Arquitetura em duas linhas

Quatro repositórios: `bramapa` (motor Rust/wgpu + spec), `bramapa-tileset` (este: arte + `tiles.toml`),
`bramapa-pipeline` (gera chunks de mapa a partir de dados geográficos) e `bramapa-data` (addons de dados).
O pipeline e o motor consomem `tiles.toml` + `atlas.png` daqui; os ids de tile são compartilhados por todos.

## Regras de geometria

- Losango base de célula: **64 × 32 px** (`tile_width × tile_height`).
- O atlas é uma grade de **cells de 64 × 64 px**; cada tile ocupa uma cell, tiles animados
  ocupam `frames` cells consecutivas na mesma linha.
- Âncora de todo tile: **centro inferior da cell**. Terreno fica encostado na base da cell;
  objetos (árvore, prédio, casa) crescem para cima a partir do centro do losango.
- Escala de exibição **sempre inteira** (1x, 2x, 3x…). **Filtragem nearest**, nunca linear.
- PNG RGBA, alfa reto (sem premultiply), sem antialiasing.
- Máscara de autotiling: 4 bits, N=1, E=2, S=4, W=8 = vizinhos com a mesma cobertura.

## Ids de tile (fixos entre repositórios)

| Classe | Ids |
|---|---|
| reservado | `0` = vazio |
| `terrain` | 1 campo · 2 agua (4 frames) · 3 oceano (4 frames) · 4 floresta · 5 areia · 6 urbano · 7 montanha · 8 savana · 9 agricultura · 10 pastagem · 11 caatinga · 12 pantanal |
| `terrain` (variantes de litoral) | `100 + máscara` = agua_m00..agua_m15 (100..115) · `120 + máscara` = oceano_m00..oceano_m15 (120..135), todas com 4 frames |
| `overlay` | 20 rodovia · 21 rio (4 frames) · 22 limite_estado · 23 limite_municipio · 24 limite_pais · 25 ferrovia |
| `object` | 40 arvore · 41 predio · 42 casa |

A posição de cada um no atlas está em `tiles.toml`, que é a fonte da verdade: o gerador lê
`col/row/frames` de lá e o `check` confere que o PNG bate.

## Variantes de litoral (Fase 1 — autotiling visível)

`agua` e `oceano` têm 16 variantes cada, uma por máscara de vizinhança, para que a espuma
apareça onde a água encontra outra cobertura. A regra `[[autotile]]` de cada uma lista as 16
máscaras; o id é uma fórmula pura:

- `agua`: id = **100 + máscara** (100..115), `default = 2`
- `oceano`: id = **120 + máscara** (120..135), `default = 3`

A máscara tem 4 bits — **N = 1** `(x, y−1)`, **E = 2** `(x+1, y)`, **S = 4** `(x, y+1)`,
**W = 8** `(x−1, y)` — e um bit **ligado** significa "vizinho com a mesma cover". Logo um bit
**desligado** é "margem daquele lado", e é nessa aresta do losango que a variante ganha uma linha
de espuma de 2 px (mais clara, animada junto com os 4 frames da água). A máscara 15 (cercado)
é pixel por pixel igual ao tile base; existe só para a fórmula não ter exceção.

**Qual aresta do losango cada vizinho toca** (derivado da projeção, não da bússola): a célula
`(x, y)` fica na tela em `sx = (x − y) · 32`, `sy = (x + y) · 16`, então

| Vizinho | Delta na grade | Delta na tela | Aresta do losango |
|---|---|---|---|
| N `(x, y−1)` | `(0, −1)` | `(+32, −16)` → acima à direita | **nordeste** (superior direita) |
| E `(x+1, y)` | `(+1, 0)` | `(+32, +16)` → abaixo à direita | **sudeste** (inferior direita) |
| S `(x, y+1)` | `(0, +1)` | `(−32, +16)` → abaixo à esquerda | **sudoeste** (inferior esquerda) |
| W `(x−1, y)` | `(−1, 0)` | `(−32, −16)` → acima à esquerda | **noroeste** (superior esquerda) |

Exemplo: máscara 14 (`0b1110`, só N desligado) → espuma só na aresta superior direita.

As 128 cells novas (2 covers × 16 máscaras × 4 frames) ocupam as rows 4–7 (agua) e 8–11
(oceano) do atlas, que cresceu de 1024 × 256 para **1024 × 1024**; as cells dos ids 1..42 não
mudaram de lugar. `rio` continua com a regra estrutural da Fase 0 (todas as máscaras → id 21),
e o `check` avisa (`WARN`, sem falhar) quando uma regra não lista as 16 máscaras e depende do
`default`; só falha se faltar máscara **e** não houver `default`.

## Como regenerar o placeholder

Requer [uv](https://docs.astral.sh/uv/) e Python ≥ 3.12. O `atlas.png` gerado é
**commitado** no repositório, então quem só consome o tileset não precisa de Python.

```sh
uv sync                              # cria o venv com Pillow, numpy e pytest
uv run bramapa-tileset generate      # reescreve atlas.png a partir de tiles.toml
uv run bramapa-tileset check         # confere tiles.toml x atlas.png (exit 1 se houver problema)
uv run pytest                        # determinismo, check verde, fixture quebrada falha
```

`generate --out caminho.png` escreve em outro lugar; `check --tiles x.toml --atlas y.png` checa
arquivos arbitrários. A geração é determinística (hash posicional, sem RNG): rodar duas vezes
produz os mesmos bytes, e o teste `test_generate_is_deterministic` garante que o `atlas.png`
commitado é exatamente o que o código gera.

### O que o `check` verifica

- `spec_version = 0`, campos de `[tileset]` presentes, geometria positiva e losango cabendo na cell.
- Cada tile: id único em 1..65535 (0 nunca declarado), nome único, `class` ∈ {terrain, overlay, object},
  `frames ≥ 1`, `fps > 0` se e só se animado, `cover` obrigatório em terrain/overlay.
- Cells `col..col+frames-1` na `row` cabem no `atlas.png` e não se sobrepõem a outro tile.
- Atlas RGBA com tamanho múltiplo da cell.
- `[[autotile]]`: cover existe em algum tile, `default` e `masks` apontam para ids existentes com
  a mesma cover, chaves de máscara em 0..15, e toda máscara 0..15 resolve (listada ou via `default`;
  `WARN` quando depende do `default`, erro se não há `default`).

## Onde a arte de verdade vai

- `src-art/` — fontes Aseprite (`.aseprite`) por classe/tile. Vazio por enquanto (`.gitkeep`).
- `atlas.png` + `tiles.toml` — continuam sendo o produto final; quando a arte real existir,
  o gerador procedural vira um fallback/ferramenta de layout e o export do Aseprite passa a
  preencher as cells. A convenção de ids e cells **não muda** com a troca de arte.

## Licenças

- Ferramental (`src/`, `tests/`, `pyproject.toml`): MIT **ou** Apache-2.0, à sua escolha
  (`LICENSE-MIT`, `LICENSE-APACHE`).
- Arte (`atlas.png`, `src-art/`): **CC-BY-4.0** (`LICENSE-ART.md`).
