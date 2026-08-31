import Testing
@testable import MacDeckCore

@Test func senzaDeckLaBarraNonLoDaPerPresente() {
    let s = Salute(deck: nil, announced: nil, error: nil, lastRound: 3.0)
    #expect(s.deckInRete == false)
}

@Test func conUnIndirizzoIlDeckEInRete() {
    let s = Salute(deck: "192.168.0.174", announced: "192.168.0.165",
                   error: nil, lastRound: 3.0)
    #expect(s.deckInRete)
}
