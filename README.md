# Betaloop pour Home Assistant

Intégration Home Assistant qui lit les données du [serveur Betaloop](https://gitea.midgar.me/syrma/Betaloop-server)
(glycémie, tendance, statistiques, insuline, glucides, pas) et les expose sous forme de
capteurs, avec une carte de tableau de bord prête à l'emploi pour le graphique.

L'intégration ne fait que **lire** : elle interroge les routes `GET /api/v1/*` du serveur
avec la même clé API que l'app, et n'écrit jamais rien.

## Installation

### Via HACS (recommandé)

1. HACS > menu ⋮ > **Dépôts personnalisés**
2. URL : `https://gitea.midgar.me/syrma/Betaloop-HomeAssistant`, catégorie **Intégration**
3. Installe **Betaloop**, puis redémarre Home Assistant.

> HACS doit pouvoir lire le dépôt : il doit être public sur Gitea (ou installe à la main).

### À la main

Copie le dossier `custom_components/betaloop` dans le dossier `config/custom_components/`
de Home Assistant, puis redémarre.

## Configuration

**Paramètres > Appareils et services > Ajouter une intégration > Betaloop**

- **URL du serveur** : par ex. `http://192.168.1.10:8000` (la même que dans l'app)
- **Clé API** : la valeur de `BETALOOP_API_KEY` dans le `.env` du serveur

Options (bouton **Configurer** sur l'intégration) :

| Option | Défaut | Rôle |
|---|---|---|
| Intervalle d'interrogation | 5 min | Fréquence d'appel au serveur. L'app ne synchronise que toutes les ~15 min. |
| Fenêtre d'historique | 24 h | Durée de relevés/doses/repas exposée pour le graphique (max 72 h). |

Si la clé API change sur le serveur, Home Assistant affiche une notification pour saisir
la nouvelle.

## Capteurs

| Entité | Description |
|---|---|
| `sensor.betaloop_glucose` | Dernière glycémie (mg/dL). *Inconnue* si le dernier relevé a plus de 45 min. Attribut `readings` : la série pour le graphique. |
| `sensor.betaloop_trend` | Tendance : baisse rapide / baisse / stable / hausse / hausse rapide |
| `sensor.betaloop_last_reading` | Horodatage du dernier relevé reçu par le serveur |
| `sensor.betaloop_average_24h` | Glycémie moyenne sur 24 h |
| `sensor.betaloop_time_in_range_24h` | % de temps dans la cible 70–180 mg/dL sur 24 h |
| `sensor.betaloop_time_below_range_24h` | % sous 70 mg/dL sur 24 h |
| `sensor.betaloop_time_above_range_24h` | % au-dessus de 180 mg/dL sur 24 h |
| `sensor.betaloop_estimated_hba1c` | HbA1c estimée sur 14 jours (formule ADAG, comme le serveur) |
| `sensor.betaloop_insulin` | Total d'insuline sur la fenêtre d'historique ; attributs : dernière dose, liste `doses` |
| `sensor.betaloop_carbs` | Total de glucides sur la fenêtre d'historique ; attributs : dernier repas, liste `meals` |
| `sensor.betaloop_steps_today` | Pas depuis minuit |

Comme `sensor.betaloop_glucose` utilise la classe *concentration de glucose sanguin*, tu
peux l'afficher en mmol/L depuis les paramètres de l'entité. Les séries en attributs
(`readings`, `doses`, `meals`) restent en mg/dL, en epoch millisecondes, et ne sont pas
enregistrées dans la base du recorder (elles sont rechargées à chaque interrogation).

## Graphique dans le tableau de bord

La carte [`dashboard/betaloop-card.yaml`](dashboard/betaloop-card.yaml) affiche la
glycémie des dernières 24 h colorée selon la plage cible (rouge < 70, vert, orange > 180),
avec la bande cible en fond, les doses d'insuline et les glucides en barres, et une ligne
de tuiles (tendance, temps dans la cible, moyenne, HbA1c, insuline, glucides, pas).

1. Installe la carte **apexcharts-card** via HACS (catégorie *Interface*).
2. Tableau de bord > **Modifier** > **Ajouter une carte** > **Manuel**, colle le contenu du
   fichier.

Le graphique lit directement l'historique fourni par le serveur : il est complet dès
l'installation, sans attendre que Home Assistant accumule son propre historique.

Sans apexcharts-card, une carte native fonctionne aussi, mais seulement avec l'historique
enregistré par Home Assistant depuis l'installation :

```yaml
type: history-graph
hours_to_show: 24
entities:
  - sensor.betaloop_glucose
```

## Réseau

Home Assistant doit pouvoir joindre le serveur. Si le serveur n'est accessible que via
Tailscale, installe l'add-on Tailscale sur Home Assistant, ou utilise l'adresse locale si
les deux sont sur le même réseau.
