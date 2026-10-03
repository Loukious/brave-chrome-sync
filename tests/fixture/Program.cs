using System.Text.Json;

var output = args.First(argument => argument.StartsWith("--fixture-args="))["--fixture-args=".Length..];
File.WriteAllText(output, JsonSerializer.Serialize(args));
