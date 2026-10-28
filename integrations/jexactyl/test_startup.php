<?php
// Run the real supplied service against an in-memory database boundary.
namespace Illuminate\Database {
    interface ConnectionInterface { public function transaction($callback); }
    class TestConnection implements ConnectionInterface {
        public function transaction($callback) { return $callback(); }
    }
}
namespace Illuminate\Support {
    class Arr { public static function get($data, $key) { return $data[$key] ?? null; } }
}
namespace Everest\Models {
    class User { const USER_LEVEL_ADMIN = 1; }
    class ServerVariable {}
    class Egg {
        public static string $command;
        public static function query() { return new self; }
        public function findOrFail($id) { return (object)['id' => (int)$id, 'nest_id' => 1, 'startup' => self::$command]; }
    }
    class Server {
        public int $egg_id = 36;
        public int $nest_id = 1;
        public string $startup = 'python -m endstone -s . -y';
        public string $image = 'ghcr.io/parkervcp/yolks:python_3.14';
        public bool $skip_scripts = false;
        public function fill($data) { foreach ($data as $key => $value) { $this->$key = $value; } return $this; }
        public function forceFill($data) { return $this->fill($data); }
        public function save() { return true; }
        public function fresh() { return $this; }
    }
}
namespace Everest\Traits\Services {
    trait HasUserLevels {
        private int $level = 1;
        public function setUserLevel($value) { $this->level = $value; return $this; }
        public function getUserLevel() { return $this->level; }
        public function isUserLevel($value) { return $this->level === $value; }
    }
}
namespace Everest\Services\Servers { class VariableValidatorService {} }
namespace {
    function is_digit($value) { return ctype_digit((string)$value); }
    require $argv[1];
    $egg = json_decode(file_get_contents($argv[2]), true, flags: JSON_THROW_ON_ERROR);
    \Everest\Models\Egg::$command = $egg['startup'];
    $service = new \Everest\Services\Servers\StartupModificationService(
        new \Illuminate\Database\TestConnection,
        new \Everest\Services\Servers\VariableValidatorService
    );
    $check = function ($condition, $name) {
        if (!$condition) { throw new \RuntimeException($name); }
        echo "PASS: $name\n";
    };
    foreach ([null, '', '   '] as $blank) {
        $server = new \Everest\Models\Server;
        $result = $service->handle($server, ['egg_id' => 39, 'startup' => $blank]);
        $check($result->startup === $egg['startup'] && $result->egg_id === 39, 'Changed Egg applies complete default startup (' . var_export($blank, true) . ')');
    }
    $result = $service->handle(new \Everest\Models\Server, ['egg_id' => 39]);
    $check($result->startup === $egg['startup'], 'Changed Egg without startup applies default');
    $result = $service->handle(new \Everest\Models\Server, ['egg_id' => 39, 'startup' => 'custom-command']);
    $check($result->startup === 'custom-command', 'Explicit custom command is preserved');
    $result = $service->handle(new \Everest\Models\Server, ['egg_id' => 36, 'startup' => null]);
    $check($result->startup === 'python -m endstone -s . -y', 'Same Egg retains existing command');
    $service->setUserLevel(0);
    $result = $service->handle(new \Everest\Models\Server, ['egg_id' => 39, 'startup' => 'unauthorized']);
    $check($result->egg_id === 36 && $result->startup === 'python -m endstone -s . -y', 'Non-admin cannot replace Egg or startup');
}
