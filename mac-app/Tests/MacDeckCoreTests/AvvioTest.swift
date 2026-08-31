import Testing
@testable import MacDeckCore

@Test func seLAgentRispondeNonSiToccaNiente() async {
    var kickstartChiamato = false
    let avvio = Avvio(
        agentRisponde: { true },
        launchAgentCaricato: { Issue.record("non va nemmeno guardato"); return true },
        kickstart: { kickstartChiamato = true; return true },
        attendi: { },
        tentativi: 3)
    #expect(await avvio.esegui() == .pronto)
    #expect(kickstartChiamato == false)
}

@Test func seLAgentTaceMaIlServizioCEIlRiavvioLoRimetteInPiedi() async {
    var risposte = [false, false, true]
    let avvio = Avvio(
        agentRisponde: { risposte.isEmpty ? true : risposte.removeFirst() },
        launchAgentCaricato: { true },
        kickstart: { true },
        attendi: { },
        tentativi: 5)
    #expect(await avvio.esegui() == .pronto)
}

@Test func seNonRipartePrimaDeiTentativiCiSiArrende() async {
    let avvio = Avvio(
        agentRisponde: { false },
        launchAgentCaricato: { true },
        kickstart: { true },
        attendi: { },
        tentativi: 3)
    #expect(await avvio.esegui() == .nonRiparte)
}

@Test func senzaLaunchAgentNonSiInventaNulla() async {
    var kickstartChiamato = false
    let avvio = Avvio(
        agentRisponde: { false },
        launchAgentCaricato: { false },
        kickstart: { kickstartChiamato = true; return true },
        attendi: { },
        tentativi: 3)
    #expect(await avvio.esegui() == .launchAgentAssente)
    // la regola che conta: nessun eseguibile avviato di propria iniziativa
    #expect(kickstartChiamato == false)
}
