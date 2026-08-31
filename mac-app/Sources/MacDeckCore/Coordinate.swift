/// Un punto nel sistema del DOM: origine in alto a sinistra.
public struct PuntoViewport: Equatable, Sendable {
    public let x: Double
    public let y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

/// AppKit misura dal basso, il DOM dall'alto — ma solo nelle viste "normali".
///
/// `WKWebView` e' `isFlipped == true`: la sua origine e' gia' in alto a
/// sinistra come nel DOM, e `NSView.convert(_:from:)` lo sa e ha gia' fatto
/// il lavoro per noi. Applicare comunque il ribaltamento significherebbe
/// ribaltare due volte, mandando i drop sulla riga opposta a quella giusta.
/// Percio' questa funzione prende `vistaCapovolta` esplicito invece di
/// assumere sempre AppKit-normale: quando e' `true` la y passa cosi' com'e',
/// quando e' `false` (il caso comune, non-flipped) resta `altezzaVista - y`.
///
/// Il risultato e' in coordinate del viewport, che e' esattamente cio' che
/// `document.elementFromPoint` si aspetta: nessuno scroll da compensare.
public func puntoViewport(daVistaX x: Double, daVistaY y: Double,
                          altezzaVista: Double,
                          vistaCapovolta: Bool) -> PuntoViewport {
    PuntoViewport(x: x, y: vistaCapovolta ? y : altezzaVista - y)
}
