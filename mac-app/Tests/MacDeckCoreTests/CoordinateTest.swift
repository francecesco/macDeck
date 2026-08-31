import Testing
@testable import MacDeckCore

@Test func lOrigineDiAppKitStaInBassoQuellaDelDomInAlto() {
    // vista alta 300: il punto a y=250 in AppKit e' a 50 dal bordo alto
    let p = puntoViewport(daVistaX: 40, daVistaY: 250, altezzaVista: 300)
    #expect(p.x == 40)
    #expect(p.y == 50)
}

@Test func ilBordoAltoRestaZero() {
    let p = puntoViewport(daVistaX: 0, daVistaY: 300, altezzaVista: 300)
    #expect(p.y == 0)
}
