# Betaloop pour Home Assistant

Intégration Home Assistant qui lit les données du [serveur Betaloop](https://gitea.midgar.me/syrma/Betaloop-server)
(glycémie, tendance, variation, statistiques, insuline, glucides, pas, expiration du capteur) et les expose sous forme de
capteurs, avec une carte de tableau de bord prête à l'emploi pour le graphique.

L'intégration ne fait que **lire** : elle interroge les routes `GET /api/v1/*` du serveur
avec la même clé API que l'app, et n'écrit jamais rien.

## Installation

### Via HACS (conseillé)

1. Dans HACS > ⋮ > **Dépôts personnalisés**, ajoute `https://github.com/Syrmack/Betaloop-HomeAssistant`,
   catégorie **Intégration**.
2. Cherche **Betaloop** dans HACS, télécharge-le, puis redémarre Home Assistant.

HACS signale ensuite les nouvelles versions.

Le dépôt principal est sur [Gitea](https://gitea.midgar.me/syrma/Betaloop-HomeAssistant). Le dépôt GitHub
en est un miroir à sens unique : ce qui y est modifié directement est écrasé au push suivant.

### À la main

Copie le dossier `custom_components/betaloop` dans le dossier `config/custom_components/`
de Home Assistant (via l'add-on *Samba share*, *File editor* ou *Terminal & SSH*), puis
redémarre. Pour une mise à jour, remplace le dossier et redémarre.

Depuis l'add-on *Terminal & SSH*, en une commande :

```bash
cd /tmp && rm -rf betaloop-ha \
  && git clone --depth 1 https://gitea.midgar.me/syrma/Betaloop-HomeAssistant.git betaloop-ha \
  && rm -rf /config/custom_components/betaloop \
  && mkdir -p /config/custom_components \
  && cp -r betaloop-ha/custom_components/betaloop /config/custom_components/ \
  && ha core restart
```

## Configuration

**Paramètres > Appareils et services > Ajouter une intégration > Betaloop**

- **URL du serveur** : par ex. `http://192.168.1.10:8000` (la même que dans l'app)
- **Clé API** : de préférence une clé dédiée avec le seul droit `read`, créée sur le serveur
  (1.9 ou plus) avec `POST /api/v1/keys` et révocable ; sinon la valeur de `BETALOOP_API_KEY`
  dans le `.env` du serveur. L'intégration ne fait que lire.

Options (bouton **Configurer** sur l'intégration) :

| Option | Défaut | Rôle |
|---|---|---|
| Intervalle d'interrogation | 5 min | Fréquence d'appel au serveur. L'app synchronise à chaque relevé (toutes les 5 min par défaut). Le GMI sur 14 jours, la version du serveur, le capteur actif et les seuils de l'app, qui ne bougent presque pas, ne sont relus que toutes les heures. |
| Fenêtre d'historique | 24 h | Durée de relevés/doses/repas exposée pour le graphique (max 72 h). |

Pour changer l'URL du serveur ou la clé API : menu **⋮ > Reconfigurer** sur l'intégration.
La clé laissée vide reste la même, et la nouvelle valeur est vérifiée auprès du serveur avant
d'être enregistrée. Si le serveur refuse la clé (révoquée, expirée ou changée), Home Assistant
affiche aussi une notification pour en saisir une nouvelle.

## Capteurs

| Entité | Description |
|---|---|
| `sensor.betaloop_glucose` | Dernière glycémie (mg/dL). *Inconnue* si le dernier relevé a plus de 45 min. Attribut `readings` : la série pour le graphique ; `target_low` / `target_high` : les seuils de l'app. |
| `sensor.betaloop_trend` | Tendance : baisse rapide / baisse / stable / hausse / hausse rapide |
| `sensor.betaloop_glucose_delta` | Variation (mg/dL) depuis le relevé précédent, par ex. `+4` ; attribut `minutes` : l'écart entre les deux relevés (1 min en surveillance continue, 15 min dans l'historique LibreLinkUp). *Inconnue* si le dernier relevé est périmé ou si le précédent a plus de 20 min. |
| `sensor.betaloop_predicted_glucose` | Glycémie prévue dans 1 h (mg/dL), le même calcul que la courbe « Prévision » de l'app : tant qu'une dose d'insuline rapide agit ou qu'un repas aux glucides saisis s'absorbe, avec les paramètres du calculateur de bolus. Attributs : `in_30min`, `in_120min`, `until` (fin d'action ou d'absorption), `forecast` (la courbe, pour le graphique) et `reason` (pourquoi elle est *inconnue* : rien n'agit, pas de relevé depuis 15 min, repas sans glucides, paramètres pas encore envoyés par l'app). Nécessite le serveur 1.14. Une projection, pas une certitude. |
| `sensor.betaloop_predicted_low` | Point le plus bas de la prévision (mg/dL) ; attribut `time`. Pratique pour une automatisation « hypo prévue » (sous 70). |
| `sensor.betaloop_predicted_high` | Point le plus haut de la prévision (mg/dL), le pic après un repas ; attribut `time`. |
| `sensor.betaloop_last_reading` | Horodatage du dernier relevé reçu par le serveur |
| `sensor.betaloop_average_24h` | Glycémie moyenne sur 24 h |
| `sensor.betaloop_time_in_range_24h` | % de temps dans la cible sur 24 h, entre les seuils réglés dans l'app (70–180 mg/dL par défaut) |
| `sensor.betaloop_time_below_range_24h` | % sous le seuil bas sur 24 h |
| `sensor.betaloop_time_above_range_24h` | % au-dessus du seuil haut sur 24 h |
| `sensor.betaloop_estimated_hba1c` | GMI (HbA1c estimée) sur 14 jours, le même que l'onglet Stats de l'app : relevés ramenés à une valeur par quart d'heure, 3,31 + 0,02392 × moyenne. Attributs : `coverage_pct` (représentatif à partir de 70 %), `average_mgdl`, `estimated_hba1c_adag` (ancienne formule). Avec un serveur antérieur à la 1.8, HbA1c estimée ADAG (`formula`). |
| `sensor.betaloop_insulin` | Total d'insuline sur la fenêtre d'historique ; attributs : dernière dose, liste `doses` |
| `sensor.betaloop_carbs` | Total de glucides sur la fenêtre d'historique ; attributs : dernier repas, liste `meals` |
| `sensor.betaloop_steps_today` | Pas depuis minuit |
| `sensor.betaloop_sensor_expiry` | Date d'expiration du capteur CGM (activation + durée de vie réglée dans l'app) ; attributs : numéro de série, activation, durée de vie. Nécessite le serveur 1.5. |
| `sensor.betaloop_sensor_days_left` | Jours restants avant expiration du capteur (0 une fois expiré) |

Les seuils de la plage cible sont ceux réglés dans l'app, que le serveur reçoit à partir de
la 1.7 (attributs `target_low` / `target_high` de la glycémie et des capteurs de temps dans
la cible). Avec un serveur plus ancien, ce sont ceux par défaut, 70 et 180 mg/dL.

Comme `sensor.betaloop_glucose` utilise la classe *concentration de glucose sanguin*, tu
peux l'afficher en mmol/L depuis les paramètres de l'entité. Les séries en attributs
(`readings`, `doses`, `meals`, `forecast`) restent en mg/dL, en epoch millisecondes, et ne sont pas
enregistrées dans la base du recorder (elles sont rechargées à chaque interrogation).

## Graphique dans le tableau de bord

La carte [`dashboard/betaloop-card.yaml`](dashboard/betaloop-card.yaml) affiche la
glycémie des dernières 24 h colorée selon la plage cible (rouge sous le seuil bas, vert,
orange au-dessus du seuil haut, rouge au-delà de 250 mg/dL), avec la bande cible en fond, les
doses d'insuline et les glucides en barres, et une ligne de tuiles (tendance, variation, temps
dans la cible, moyenne, HbA1c, insuline, glucides, pas, expiration du capteur).

Les seuils du graphique (couleurs, bande et lignes pointillées) sont ceux réglés dans l'app,
lus dans les attributs `target_low` / `target_high` de `sensor.betaloop_glucose` : un
changement dans l'app arrive sur le graphique à la synchro suivante, puis dans l'heure côté
Home Assistant. Avec un serveur antérieur à la 1.7, ce sont ceux par défaut, 70 et 180 mg/dL.

1. Installe les cartes **apexcharts-card** et **config-template-card** via HACS (catégorie
   *Interface*). config-template-card sert à passer les seuils au graphique : apexcharts-card
   seule n'accepte que des valeurs fixes.
2. Tableau de bord > **Modifier** > **Ajouter une carte** > **Manuel**, colle le contenu du
   fichier.

Le graphique lit directement l'historique fourni par le serveur : il est complet dès
l'installation, sans attendre que Home Assistant accumule son propre historique.

Pour ajouter la **glycémie prévue** au graphique, ajoute cette série dans `series`, et
`span: { offset: "+2h" }` sous `graph_span` pour voir les 2 prochaines heures :

```yaml
        - entity: sensor.betaloop_predicted_glucose
          name: Prévision
          yaxis_id: glucose
          type: line
          stroke_width: 2
          stroke_dash: 4
          color: "#757575"
          extend_to: false
          show:
            in_header: false
          data_generator: |
            return entity.attributes.forecast || [];
```

Sans apexcharts-card, une carte native fonctionne aussi, mais seulement avec l'historique
enregistré par Home Assistant depuis l'installation :

```yaml
type: history-graph
hours_to_show: 24
entities:
  - sensor.betaloop_glucose
```

## Icône

`custom_components/betaloop/brand/` contient l'icône de l'intégration (`icon.png` 256 px,
`icon@2x.png` 512 px) : la goutte de l'app Android sur son violet `#6750A4`. Home Assistant
l'affiche dans *Appareils et services* pour les intégrations personnalisées qui fournissent
ce dossier ; sur une version qui ne le gère pas, l'icône générique reste affichée.

## Réseau

Home Assistant doit pouvoir joindre le serveur. Si le serveur n'est accessible que via
Tailscale, installe l'add-on Tailscale sur Home Assistant, ou utilise l'adresse locale si
les deux sont sur le même réseau.

## Prochaines améliorations

### Prioritaires

Toutes faites : seuils de la plage cible lus depuis le serveur (1.7), charge réduite (6
requêtes par interrogation, prévision comprise, au lieu de 10 ; le reste relu toutes les heures).

### Probables

- **Binary sensors** pour les automatisations : hypo, hyper, données périmées, capteur
  qui expire dans moins de 24 h. Et des événements (`betaloop_hypo`...) pour, par
  exemple, faire clignoter une lampe la nuit : un second canal d'alerte, indépendant du
  téléphone.
- **Diagnostics** (`diagnostics.py`, clé API masquée) pour le dépannage.
- **Tests** avec `pytest-homeassistant-custom-component`.
- **Statistiques de référence** (serveur 1.8 : bloc `consensus` de `/api/v1/summary`, route
  `/api/v1/agp`) : CV, GMI, temps dans les 5 paliers, couverture. Pas prévu pour l'instant :
  ces indicateurs servent aux échanges avec le diabétologue (onglet Stats et rapport PDF de
  l'app), pas au suivi au quotidien que vise l'intégration.
