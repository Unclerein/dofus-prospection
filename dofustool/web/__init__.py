"""Interface web locale : un serveur HTTP minimal au-dessus des calculs d'analyse."""
import os

# numpy réserve par défaut un gros tampon de calcul par cœur du processeur (plus de 500 Mo sur 16 cœurs),
# qui ne sert à rien ici : les tableaux de l'interface sont petits. À fixer avant le premier import de numpy.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
