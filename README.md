# solweig-lyon

Analyse du confort thermique sur la métropole de Lyon à l'aide de [SOLWEIG-GPU](https://github.com/nvnsudharsan/SOLWEIG-GPU).

La méthodologie provient d'une [étude](https://www.sciencedirect.com/science/article/abs/pii/S0360132325007188) de Damien David et Marjorie Salles de Université de Lyon, UCBL, INSA Lyon, CNRS,[CETHIL](https://cethil.insa-lyon.fr/fr).

Les données d'entrée proviennent de sources ouvertes françaises ([Vegestrate](carte.iarbre.fr), fichiers météo UMEP du CETHIL, LiDAR GrandLyon, BD TOPO, COSIA). 
Les trois scénarios climatiques (2020 / 2060 / 2090), pour une journée typique au 14 juillet, proviennent de modélisation au CETHIL.

## Installation

Installer [uv](https://docs.astral.sh/uv/getting-started/installation/) si nécessaire, puis :

```bash
uv sync
```

Installe toutes les dépendances, dont `solweig-gpu` depuis le dernier commit de la [branche main](https://github.com/nvnsudharsan/SOLWEIG-GPU), et installe le paquet local `solweig_lyon` en mode editable (les scripts de `pipeline/` peuvent donc l'importer quel que soit le dossier courant).

## Structure du projet

```
solweig_lyon/          # paquet importable
  config.py            # constantes (TILE_SIZE, OVERLAP, CRS, BBOX par défaut)
  pet.py               # régression PET (pet_polynomial, PET_BINS)
  utils/              # préparation des rasters d'entrée
    buildings.py  dem.py  landcover.py  trees.py  geo.py
pipeline/              # scripts exécutables, dans l'ordre
  01_prepare_data.py
  02_run_solweig.py
  03_compute_pet.py
  04_merge_outputs.py
```

Chaque étape se lance avec `uv run python pipeline/<script>` (voir ci-dessous).

## Données

| Fichier | Description | Source |
|---|---|---|
| `data/vegestrate_02_2023_elevation.tif` | Hauteur végétation nDSM, EPSG:3946, 0,2 m | Vegestrate |
| `data/01-CURRENT_14jul.txt` | Fichier météo UMEP -- climat actuel 2020, 14 juillet | CETHIL |
| `data/02-MID-CENTURY_14jul.txt` | Fichier météo UMEP -- scénario mi-siècle 2060 | CETHIL |
| `data/03-END-CENTURY_14jul.txt` | Fichier météo UMEP -- scénario fin de siècle 2090 | CETHIL |
| `lidar_tiles.csv` | Index de 2842 tuiles LiDAR GrandLyon (500 m x 500 m) avec URLs de téléchargement | [GrandLyon](https://data.grandlyon.com/fr/datapusher/ws/grandlyon/ima_gestion_images.imamnt2023laz500mcc46/all.csv?maxfeatures=-1&filename=nuage-de-points-lidar-2023-de-la-metropole-de-lyon) |

## Étape 1 - Préparer les données

La zone par défaut de test est vers Confluence (5 x 5 km).

```bash
uv run python pipeline/01_prepare_data.py
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
uv run python pipeline/01_prepare_data.py --bbox 1839000 5171000 1841000 5173000

uv run python pipeline/01_prepare_data.py --bbox 1831000 5152000 1860500 5195000
```
On peut aussi changer la résolution de l'analyse qui est par défaut de 1m.
```bash
uv run python pipeline/01_prepare_data.py --resolution 2
```

Les tuiles LiDAR sont stockées dans `data/lidar_tiles/` et ne sont pas re-téléchargées si déjà existantes. Pareil pour les emprises des bâtiments BD TOPP qui sont stockées  dans `inputs/cache_buildings.geojson`.

### Codes d'occupation du sol UMEP

| Code | Classe |
|---|---|
| 1 | Revêtement |
| 2 | Bâtiments |
| 3 | Eau |
| 4 | Végétation |
| 5 | Sol nu |


## Étape 2 - Lancer SOLWEIG

```bash
uv run python pipeline/02_run_solweig.py
```

Lance par défaut le scénario de 2020.

### Parallélisation des tuiles

Par défaut, `pipeline/02_run_solweig.py` traite les tuiles en parallèle (2 processus) pour accélérer le calcul. Chaque processus calcule le SVF et l'UTCI pour son lot de tuiles. Les scénarios, eux, restent séquentiels.

Deux variables d'environnement permettent de régler ce comportement :

```bash
# Nombre de processus en parallèle (défaut : 2)
SOLWEIG_PARALLEL=4 uv run python pipeline/02_run_solweig.py

# Répartir les processus sur plusieurs GPU (round-robin)
SOLWEIG_GPUS=0,1 SOLWEIG_PARALLEL=2 uv run python pipeline/02_run_solweig.py

# Mode séquentiel (un seul processus, sans multiprocessing) :
# utile pour déboguer ou sur une machine sans GPU dédié
SOLWEIG_PARALLEL=1 uv run python pipeline/02_run_solweig.py
```

Attention : avec **un seul GPU partagé**, la parallélisation n'apporte qu'un léger gain (les calculs GPU se sérialisent sur le même appareil) et chaque processus consomme sa propre mémoire GPU. Trop de processus va provoquer un dépassement de mémoire (OOM). Commencer à 2 et surveiller la mémoire. Le vrai gain (×N) vient de N GPU via `SOLWEIG_GPUS`.

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

## Étape 3 - Calculer le PET

```bash
uv run python pipeline/03_compute_pet.py
```

On calcule à partir des sorties `TMRT_*.tif` (une bande par heure) et du `SVF` et des variables météo horaires (`Tair`, `U`, `RH`) du fichier météo d'entrée, via une régression polynomiale de degré 5 (`solweig_lyon/pet.py`), le **PET (Physiological Equivalent Temperature)**.

Pour chaque tuile, le script écrit à côté du `TMRT_*.tif` :

| Fichier de sortie | Contenu |
|---|---|
| `PET_<tuile>.tif` | PET en °C, float16 compressé, multi-bandes (une bande par heure) |
| `PET_index_<tuile>.tif` | Seuillage du PET, uint8 (1–9), une bande par heure |

Le seuillage se fait selon les classes standard de perception thermique PET (Matzarakis & Mayer) :

| Indice | PET [°C] | Perception |
|---|---|---|
| 1 | < 4 | stress froid extrême |
| 2 | 4 – 8 | fort stress froid |
| 3 | 8 – 13 | stress froid modéré |
| 4 | 13 – 18 | léger stress froid |
| 5 | 18 – 23 | pas de stress thermique |
| 6 | 23 – 29 | léger stress chaud |
| 7 | 29 – 35 | stress chaud modéré |
| 8 | 35 – 41 | fort stress chaud |
| 9 | > 41 | stress chaud extrême |

La valeur `0` de l'indice correspond aux pixels sans donnée. Les seuils sont définis dans `solweig_lyon/pet.py` (`PET_BINS`) si besoin de les ajuster.

## Étape 4 - Fusionner les tuiles

```bash
uv run python pipeline/04_merge_outputs.py
```

Pour chaque scénario, fusionne les tuiles en un seul raster par produit (`PET`, `PET_index`, `Shadow`) dans le dossier du scénario.

Les tuiles se chevauchent de `OVERLAP` pixels (voir `solweig_lyon/config.py`). Le chevauchement sert à supprimer les artefacts de bord. 

| Fichier de sortie | dtype |
|---|---|
| `PET.tif` | float16 |
| `PET_index.tif` | uint8 |
| `Shadow.tif` | float16 |

Sortie compressée en DEFLATE, multi-bandes (une bande par heure).

## Etape 6 - Retirer le masque des batiments

