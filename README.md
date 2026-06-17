# solweig-lyon

Analyse du confort thermique sur la métropole de Lyon à l'aide de [SOLWEIG-GPU](https://github.com/nvnsudharsan/SOLWEIG-GPU).

Prépare les données d'entrée à partir des sources ouvertes françaises (Vegestrate, fichiers météo UMEP du CETHIL, LiDAR GrandLyon, BD TOPO, COSIA) et lance trois scénarios climatiques (2020 / 2060 / 2090) pour le 14 juillet.

## Installation

Installer [uv](https://docs.astral.sh/uv/getting-started/installation/) si nécessaire, puis :

```bash
uv sync
```

Installe toutes les dépendances, dont `solweig-gpu` depuis le dernier commit de la [branche main](https://github.com/nvnsudharsan/SOLWEIG-GPU).

## Données

| Fichier | Description | Source |
|---|---|---|
| `data/vegestrate_02_2023_elevation.tif` | Hauteur végétation nDSM, EPSG:3946, 0,2 m | Vegestrate |
| `data/01-CURRENT_14jul.txt` | Fichier météo UMEP -- climat actuel 2020, 14 juillet | CETHIL |
| `data/02-MID-CENTURY_14jul.txt` | Fichier météo UMEP -- scénario mi-siècle 2060 | CETHIL |
| `data/03-END-CENTURY_14jul.txt` | Fichier météo UMEP -- scénario fin de siècle 2090 | CETHIL |
| `lidar_tiles.csv` | Index de 2842 tuiles LiDAR GrandLyon (500 m x 500 m) avec URLs de téléchargement | [GrandLyon](https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.csv?maxfeatures=-1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon) |

## Étape 1 - Préparer les données

La zone par défaut de test est vers Confluence (2 x 2 km, ~16 tuiles LiDAR).

```bash
uv run python prepare_data.py
```

Cette commande va télécharger les données LIDAR pour construire le DEM (Digital Elevation Model), les bâtiments de la BD TOPO et COSIA pour l'occupation des sols. Elle découpe aussi le nDSM de hauteur de végétation en tuiles car l'étape suivante se déroule sur des tuiles. On écrit les rasters correspondants dans `inputs/` :

| Fichier de sortie | Source |
|---|---|
| `Trees.tif` | Découpé et rééchantillonné depuis `vegestrate_02_2023_elevation.tif` |
| `DEM.tif` | LiDAR GrandLyon 2023 (tuiles .laz), classe sol 2 --> DTM |
| `Building_DSM.tif` | `HAUTEUR` BD TOPO rastérisé + DEM |
| `Landcover.tif` | Classes UMEP depuis COSIA : revêtement par défaut, eau, végétation (Trees > 0), bâtiments |

Pour faire sur une autre zone il faut spécifier une BBOX.

```bash
uv run python prepare_data.py --bbox 1839000 5171000 1841000 5173000

uv run python prepare_data.py --bbox 1831000 5152000 1860500 5195000
```
On peut aussi changer la résolution de l'analyse qui est par défaut de 1m.
```bash
uv run python prepare_data.py --resolution 2
```

Les tuiles LiDAR sont stockées dans `data/lidar_tiles/` et ne sont pas re-téléchargées si déjà existantes. Pareil pour les emprises des bâtiments BD TOPP qui sont stockées  dans `inputs/cache_buildings.geojson`.

## Étape 2 - Lancer SOLWEIG

```bash
uv run python run.py
```

Lance par défaut le scénario de 2020. 

On stocke comme résultat intermédiaires les SVF (sky view factor) ainsi que le calcul des ombres (TIF multi band avec les ondes heure par heure).
Ces résultats intermédiaires sont dans : 

```
inputs/processed_inputs
├── SVF/
├── walls/
├── ...
```

Les résultats UTCI et TMRT sont découpées en tuiles et stockées dans  :

```
inputs/output_folder
├── 0_0/
├── 0_1000/
├── ...
```

Chaque dossier contient des GeoTIFF par tuile (UTCI, Tmrt, ...) produits par SOLWEIG-GPU.
Les résultats UTCI sont multi-bandes avec une bande par heure (bande 0 = 00h UTC, bande 1 = 01h00 UTC, etc)

## Codes d'occupation du sol UMEP

| Code | Classe |
|---|---|
| 1 | Revêtement |
| 2 | Bâtiments |
| 3 | Eau |
| 4 | Végétation |
| 5 | Sol nu |
