/// Un punto nel sistema del DOM: origine in alto a sinistra.
public struct PuntoViewport: Equatable, Sendable {
    public let x: Double
    public let y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

/// AppKit misura dal basso, il DOM dall'alto.
///
/// Il risultato e' in coordinate del viewport, che e' esattamente cio' che
/// `document.elementFromPoint` si aspetta: nessuno scroll da compensare.
public func puntoViewport(daVistaX x: Double, daVistaY y: Double,
                          altezzaVista: Double) -> PuntoViewport {
    PuntoViewport(x: x, y: altezzaVista - y)
}
