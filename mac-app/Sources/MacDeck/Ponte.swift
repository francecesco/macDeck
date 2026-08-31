import WebKit

/// Cosa il guscio promette alla pagina.
///
/// L'oggetto viene iniettato prima che la pagina giri: dal browser non
/// esiste, e la pagina se ne accorge con `if (window.macdeck)`.
enum Ponte {
    static let sorgente = """
    window.macdeck = {
      versione: 1,
      // riempiti dalla pagina, chiamati dal guscio
      onDrop: null,
      onScorciatoia: null,
      // chiamati dalla pagina, eseguiti dal guscio
      registraScorciatoia: function () {
        window.webkit.messageHandlers.macdeck.postMessage(
          { cmd: 'registraScorciatoia' });
      }
    };
    """

    @MainActor static func userScript() -> WKUserScript {
        WKUserScript(source: sorgente,
                     injectionTime: .atDocumentStart,
                     forMainFrameOnly: true)
    }
}
