import Testing
@testable import MacDeckCore

@Test func lOrigineDiAppKitStaInBassoQuellaDelDomInAlto() {
    // vista alta 300, non capovolta: il punto a y=250 in AppKit e' a 50 dal bordo alto
    let p = puntoViewport(daVistaX: 40, daVistaY: 250, altezzaVista: 300,
                          vistaCapovolta: false)
    #expect(p.x == 40)
    #expect(p.y == 50)
}

@Test func ilBordoAltoRestaZero() {
    let p = puntoViewport(daVistaX: 0, daVistaY: 300, altezzaVista: 300,
                          vistaCapovolta: false)
    #expect(p.y == 0)
}

@Test func conVistaCapovoltaLaYPassaCosiComEra() {
    // WKWebView e' isFlipped: convert(_:from:) ha gia' fatto il lavoro,
    // ribaltare di nuovo manderebbe il drop sulla riga sbagliata.
    let p = puntoViewport(daVistaX: 40, daVistaY: 10, altezzaVista: 400,
                          vistaCapovolta: true)
    #expect(p.x == 40)
    #expect(p.y == 10)
}
