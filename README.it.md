# FragileVision

**Una leaderboard dice chi ha vinto. FragileVision dice se il risultato sopravvive a un’altra formulazione.**

FragileVision è un laboratorio locale per valutare modelli visivi e, contemporaneamente, la fragilità del benchmark con cui vengono valutati.

## Avvio

Richiede Python 3.11 o successivo e non installa dipendenze:

```bash
python3 -m fragilevision
```

L’interfaccia si apre su `http://127.0.0.1:7331`.

Nelle sezioni **Dataset** e **Modelli** sono presenti selettori nativi per scegliere una cartella senza digitare il percorso. Per Ollama e server MLX/OpenAI-compatible è disponibile anche **Rileva modelli**, che popola localmente l’elenco esposto dal provider privato.

### App macOS

```bash
bash scripts/build_macos_app.sh
```

Costruisce `dist/FragileVision.app` — un’app vera, apribile con un doppio clic
o dal Dock — usando solo strumenti già presenti su macOS (`sips`, `iconutil`,
`codesign`). Nessun bundler, nessun `pip install`: il pacchetto `fragilevision`
non ha dipendenze obbligatorie, quindi il payload è una semplice copia del
codice. Trascina il risultato su `/Applications` o sul Dock. Nessun terminale
si apre mai; un errore fatale all’avvio mostra un avviso nativo, e tutto il
resto — incluso il log delle richieste del server — va in
`~/Library/Logs/FragileVision/fragilevision.log`.

Alla prima installazione, senza nessun provider configurato, l’app controlla
l’indirizzo di default di Ollama e registra solo i modelli che Ollama stesso
dichiara capaci di visione (dal campo `capabilities` che restituisce, non da
un’indovinata sul nome). Nessun altro endpoint viene sondato automaticamente:
a differenza di Ollama, un server MLX, LM Studio o llama.cpp non condividono
una porta standard, e indovinarne una rischierebbe di registrare in silenzio
un servizio locale non correlato.

## Cosa misura

- accuratezza e balanced accuracy;
- intervalli di Wilson;
- baseline della classe maggioritaria;
- Prompt Fragility Score fra formulazioni controllate;
- Repeat Drift fra ripetizioni identiche;
- rispetto effettivo del formato richiesto;
- test esatto di McNemar sulle stesse immagini.
- accuratezza bilanciata per scena, così una raffica non pesa come molte prove indipendenti;
- Evidence Gate, una checklist euristica e trasparente sulla maturità del claim.
- Model Arena per confrontare 2–8 modelli sulle stesse unità, con intervalli al 95%, delta appaiati, vittorie/sconfitte, McNemar esatto e latenza;
- accordo fra revisori: alpha di Krippendorff, kappa di Cohen e coda di arbitrato;
- prestazioni per modello: latenza, token, tasso d’errore, ETA sulle esecuzioni in corso e un campione di memoria;
- gestione completa delle esecuzioni: rinomina, duplica, archivia, filtra ed esporta in CSV;
- report pubblicabile: grafici SVG e stampa in PDF nella Claim Card, più un'esportazione in Markdown.

## Annotazione scientifica

Una verità di riferimento scritta da una persona sola non è distinguibile dalle
abitudini di chi l’ha scritta. Ogni giudizio viene registrato con il nome del
revisore che l’ha espresso, e il valore usato dalle metriche è **derivato** da
quei giudizi, mai scritto a mano.

La regola di consenso non inventa un vincitore. Decidono l’unanimità e la
maggioranza stretta; un pareggio o una semplice pluralità no: diventano un
conflitto che vale `incerto` e resta fuori dall’accuratezza finché una persona
non arbitra. L’arbitrato porta il nome di chi lo ha deciso e si affianca alle
etichette indipendenti invece di cancellarle. Un caso su cui nessuno ha
obiettato non si può ribaltare.

La **modalità cieca** è attiva per impostazione: i giudizi altrui, il consenso e
perfino il modo in cui è stato raggiunto restano nascosti finché non hai votato.

L’alpha di Krippendorff è il coefficiente principale perché regge revisori con
sottoinsiemi diversi e panel di dimensione variabile. Il kappa di Fleiss compare
solo dove è davvero definito, il kappa di Cohen soltanto a coppie. Gli intervalli
al 95% vengono da un bootstrap sui casi, non sulle singole etichette.

Un alpha sotto 0,4 su una domanda è un avviso sulla **domanda**: se davanti a un
disaccordo devi fermarti a pensare se il tag si applica, il tag è mal definito.

## Model Arena

La Model Arena avvia lo stesso protocollo su più modelli locali. Le esecuzioni sono sequenziali, così i modelli non competono contemporaneamente per GPU o memoria unificata. È anche possibile selezionare run già completati: FragileVision raggruppa automaticamente quelli compatibili.

La classifica vale soltanto per il dataset e la configurazione selezionati. Quando sono presenti più ripetizioni, l’accuratezza conta un unico verdetto maggioritario per coppia immagine/variante, evitando di trattare chiamate ripetute come campioni indipendenti.

La fragilità e la non-deterministicità sono volutamente separate. Un modello che cambia risposta fra due esecuzioni identiche non deve far sembrare fragile una formulazione.

Nel **Mutation Lab**, il Generatore locale di stress usa uno dei provider privati configurati per proporre riformulazioni, negazioni, ambiguità, cambi di lingua, esempi, ordine, formato e lunghezza. Ogni proposta è modificabile e deve essere approvata esplicitamente prima del salvataggio.

## Prestazioni per modello

Una riga per modello configurato, cumulativa su tutti i progetti in cui è
stato usato: il costo reale di un modello è una proprietà del modello e della
macchina, non del dataset di un progetto. Una chiamata mai arrivata e una
risposta illeggibile sono contate separatamente, e ogni esecuzione in corso
mostra un tempo residuo stimato dalle ultime chiamate riuscite dello stesso
modello. La memoria è un singolo campione preso da Ollama subito dopo la prima
risposta di ogni run — non una misura continua — e non viene mai inventata per
gli endpoint OpenAI-compatibili o per il simulatore sintetico, che restano con
un trattino e una motivazione esplicita.

## Gestione delle esecuzioni

Ogni esecuzione si può rinominare, duplicare per ripartire con la stessa
identica configurazione, archiviare senza cancellarla, o eliminare.
Archiviare non tocca i dati — risposte e metriche restano intatte e
riproducibili — mette solo l’esecuzione fuori dal ledger attivo e dai
selettori di Model Arena e Failure Atlas. Un’esecuzione attiva non si può
archiviare, e una ripristinata va tolta esplicitamente dall’archivio prima di
poter essere ripresa: niente gira mai in modo invisibile. Il ledger si filtra
per progetto, stato, provider e nome, e si esporta in CSV con gli stessi
filtri applicati.

## Report pubblicabile

La Claim Card (`report.html`) porta due grafici SVG inline — la stessa
manopola circolare del Prompt Fragility Score e le stesse barre per variante
con la baseline segnata che vedi nel Failure Atlas dal vivo — così l'artefatto
esportato e l'app raccontano la stessa immagine. Nessuna libreria di grafici:
SVG puro, con gli stessi colori CSS del resto della pagina.

"Esporta PDF" richiama la stampa nativa del browser contro un foglio di stile
`@media print` che passa a una palette chiara adatta all'inchiostro e nasconde
il pulsante stesso. Nessuna libreria PDF, nessun rendering lato server.

Un'esportazione in Markdown (`report.md`) porta lo stesso riepilogo, la
checklist dell'Evidence Gate e le tabelle di varianti, confronti e affidabilità
dell'accordo, in Markdown compatibile con GitHub — utile per un README, una
wiki o un commento su un issue. Al posto dei grafici SVG, una barra a
caratteri blocco sopravvive anche in un visualizzatore di solo testo.

## Privacy

Le immagini vengono gestite localmente. Gli endpoint pubblici vengono rifiutati: sono accettati soltanto localhost, reti private e nodi Tailscale. Il Replay Bundle contiene hash, prompt, annotazioni, i giudizi di ogni singolo revisore, il rapporto sull’accordo e le risposte, ma non contiene i pixel delle immagini.

Il database della versione `0.3.0` non è cifrato a riposo: per materiale sensibile va usata la cifratura completa del disco.

Per provare subito l’intero flusso senza scaricare un modello, nella sezione **Modelli** si può attivare il Synthetic Prompt Stressor. È deterministico, non usa la rete e i risultati sono sempre marcati `DEMO`: serve a capire lo strumento, non a produrre un benchmark reale.

Per architettura, formule, test e formato degli artefatti consulta il [README principale](README.md).
