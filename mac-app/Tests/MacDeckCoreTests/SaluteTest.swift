import Foundation
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

@Test func leggeIlPayloadDellAgent() throws {
    let json = """
    {"deck":"192.168.0.174","announced":"192.168.0.165",
     "error":null,"last_round":12.4,"accessibility_ok":true}
    """.data(using: .utf8)!
    let s = try Salute.leggi(json)
    #expect(s.deck == "192.168.0.174")
    #expect(s.lastRound == 12.4)
    #expect(s.deckInRete)
}

@Test func iCampiVuotiArrivanoComeNulli() throws {
    let json = """
    {"deck":null,"announced":null,"error":null,
     "last_round":null,"accessibility_ok":null}
    """.data(using: .utf8)!
    let s = try Salute.leggi(json)
    #expect(s.deck == nil)
    #expect(s.lastRound == nil)
    #expect(s.deckInRete == false)
}
