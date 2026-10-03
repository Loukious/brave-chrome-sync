using System.Text.Json;

var output = args.First(argument => argument.StartsWith("--fixture-args="))["--fixture-args=".Length..];
File.WriteAllText(output, JsonSerializer.Serialize(args));
var pid = args.FirstOrDefault(argument => argument.StartsWith("--fixture-pid="));
if (pid != null) File.WriteAllText(pid["--fixture-pid=".Length..], Environment.ProcessId.ToString());
var wait = args.FirstOrDefault(argument => argument.StartsWith("--fixture-wait="));
if (wait != null)
{
    // Keep the simulated browser alive until the test explicitly releases it.
    var deadline = DateTime.UtcNow.AddMinutes(5);
    while (!File.Exists(wait["--fixture-wait=".Length..]) && DateTime.UtcNow < deadline)
        Thread.Sleep(100);
}
