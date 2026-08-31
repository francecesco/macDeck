import Foundation

/// Cosa l'app sa dello stato di sotto. Riempito davvero nel Task 6.
public struct Salute: Equatable, Sendable {
    public let deck: String?
    public let announced: String?
    public let error: String?
    public let lastRound: Double?

    public init(deck: String?, announced: String?,
                error: String?, lastRound: Double?) {
        self.deck = deck
        self.announced = announced
        self.error = error
        self.lastRound = lastRound
    }

    /// Il deck e' raggiungibile adesso?
    public var deckInRete: Bool { deck != nil }
}

extension Salute: Decodable {
    enum CodingKeys: String, CodingKey {
        case deck, announced, error
        case lastRound = "last_round"
    }

    /// Legge la risposta di GET /api/health cosi' com'e'.
    public static func leggi(_ dati: Data) throws -> Salute {
        try JSONDecoder().decode(Salute.self, from: dati)
    }
}
