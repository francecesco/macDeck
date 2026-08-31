import Testing
@testable import MacDeckCore

// I valori grezzi di NSEvent.ModifierFlags, presi come numeri perche'
// MacDeckCore non importa AppKit: e' il confine che tiene i test veloci.
let CMD: UInt = 1 << 20
let SHIFT: UInt = 1 << 17
let CTRL: UInt = 1 << 18
let OPT: UInt = 1 << 19

@Test func riconosceUnSoloModificatore() {
    #expect(modificatori(daFlag: CMD) == ["cmd"])
}

@Test func lOrdineEQuelloCanonico() {
    #expect(modificatori(daFlag: SHIFT | OPT | CTRL | CMD)
            == ["cmd", "ctrl", "opt", "shift"])
}

@Test func senzaModificatoriLElencoEVuoto() {
    #expect(modificatori(daFlag: 0).isEmpty)
}

@Test func iBitDiServizioNonDiventanoModificatori() {
    // NSEvent accende anche bit che non ci interessano (tastierino, caps):
    // non devono finire nell'elenco
    #expect(modificatori(daFlag: CMD | (1 << 16)) == ["cmd"])
}
