// SPDX-License-Identifier: MPL-2.0
using System.Diagnostics;
using System.Text.Json;

namespace SyncBrowser;

public sealed record GoogleClientConfiguration(string ApiKey, string ClientId, string ClientSecret)
{
    public static readonly string[] Variables = ["GOOGLE_API_KEY", "GOOGLE_DEFAULT_CLIENT_ID", "GOOGLE_DEFAULT_CLIENT_SECRET"];

    private static bool Configured(string? value) => !string.IsNullOrWhiteSpace(value) && value != "dummytoken";

    public static void Apply(ProcessStartInfo start, GoogleClientConfiguration? defaults = null)
    {
        bool Present(string name) => start.Environment.TryGetValue(name, out var value) && Configured(value);
        if (Variables.All(Present)) return;
        // A custom OAuth client must always supply its own matching secret.
        if (Present(Variables[1]) != Present(Variables[2]))
            throw new InvalidDataException("Google OAuth client configuration is incomplete.");
        if (defaults == null)
        {
            using var stream = typeof(GoogleClientConfiguration).Assembly.GetManifestResourceStream("SyncBrowser.GoogleClientConfiguration.json");
            if (stream != null) defaults = JsonSerializer.Deserialize(stream, Models.Default.GoogleClientConfiguration);
        }
        if (defaults == null || !Configured(defaults.ApiKey) || !Configured(defaults.ClientId) || !Configured(defaults.ClientSecret))
            throw new InvalidDataException("This browser package is missing Google sign-in configuration. Repair it with the updated installer.");
        var values = new[] { defaults.ApiKey, defaults.ClientId, defaults.ClientSecret };
        for (var index = 0; index < Variables.Length; index++)
            if (!Present(Variables[index])) start.Environment[Variables[index]] = values[index];
    }
}
