import AppKit
import WebKit
import MacDeckCore

/// Intercetta i file trascinati prima che ci pensi WebKit.
///
/// E' l'unico punto in cui il percorso del bundle esiste ancora: dentro la
/// pagina un file trascinato arriva come File senza percorso, ed e'
/// precisamente il motivo per cui l'app nativa esiste.
final class VistaWebConDrop: WKWebView {
    override func draggingEntered(_ s: any NSDraggingInfo) -> NSDragOperation {
        percorsi(da: s).isEmpty ? super.draggingEntered(s) : .copy
    }

    override func draggingUpdated(_ s: any NSDraggingInfo) -> NSDragOperation {
        percorsi(da: s).isEmpty ? super.draggingUpdated(s) : .copy
    }

    override func performDragOperation(_ s: any NSDraggingInfo) -> Bool {
        let elenco = percorsi(da: s)
        guard !elenco.isEmpty else { return super.performDragOperation(s) }

        let nellaVista = convert(s.draggingLocation, from: nil)
        let p = puntoViewport(daVistaX: nellaVista.x, daVistaY: nellaVista.y,
                              altezzaVista: bounds.height)

        let dati = try! JSONSerialization.data(
            withJSONObject: ["x": p.x, "y": p.y, "paths": elenco])
        let json = String(decoding: dati, as: UTF8.self)
        // Il guscio consegna e basta: quale slot sia, lo sa la pagina.
        evaluateJavaScript(
            "window.macdeck && window.macdeck.onDrop && "
            + "window.macdeck.onDrop(\(json))")
        return true
    }

    private func percorsi(da s: any NSDraggingInfo) -> [String] {
        let opzioni: [NSPasteboard.ReadingOptionKey: Any] =
            [.urlReadingFileURLsOnly: true]
        let url = s.draggingPasteboard.readObjects(
            forClasses: [NSURL.self], options: opzioni) as? [URL] ?? []
        return url.map(\.path)
    }
}
