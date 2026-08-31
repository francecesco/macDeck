import Foundation

public enum EsitoAvvio: Equatable, Sendable {
    case pronto
    case nonRiparte
    case launchAgentAssente
}

/// La sequenza d'avvio, con il mondo esterno iniettato.
///
/// Stessa scelta che l'agent fa con `executor.py`: i comandi veri stanno
/// fuori, cosi' i test girano senza agent, senza rete e senza dormire.
///
/// Non e' `Sendable`: vive e muore dentro un unico `Task { @MainActor in ... }`
/// e non attraversa mai un confine di isolamento, quindi non serve fingere
/// che le sue chiusure siano `@Sendable` — cosa che, tra l'altro, romperebbe
/// i test qui sotto: catturano `var` locali mutabili, e sotto Swift 6 una
/// chiusura `@Sendable` non puo' mutare una `var` catturata.
public struct Avvio {
    public var agentRisponde: () async -> Bool
    public var launchAgentCaricato: () -> Bool
    public var kickstart: () -> Bool
    public var attendi: () async -> Void
    public var tentativi: Int

    public init(agentRisponde: @escaping () async -> Bool,
                launchAgentCaricato: @escaping () -> Bool,
                kickstart: @escaping () -> Bool,
                attendi: @escaping () async -> Void,
                tentativi: Int = 33) {
        self.agentRisponde = agentRisponde
        self.launchAgentCaricato = launchAgentCaricato
        self.kickstart = kickstart
        self.attendi = attendi
        self.tentativi = tentativi
    }

    public func esegui() async -> EsitoAvvio {
        if await agentRisponde() { return .pronto }
        guard launchAgentCaricato() else { return .launchAgentAssente }
        _ = kickstart()
        for _ in 0..<tentativi {
            await attendi()
            if await agentRisponde() { return .pronto }
        }
        return .nonRiparte
    }
}
