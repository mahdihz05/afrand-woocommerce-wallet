<?php
// Isolated version guard contract, without WordPress, database or wallet access.
define('ABSPATH', __DIR__.'/');
function register_activation_hook($file,$callback) {}
function add_action($event,$callback) {}
class VersionDatabase {
    public $version;
    function get_var($query) { if ($query!=='SELECT VERSION()') throw new Exception('Unexpected query'); return $this->version; }
}
$wpdb=new VersionDatabase();
require __DIR__.'/../wordpress/afrand-wallet-bridge/afrand-wallet-bridge.php';
foreach (array('5.6.51'=>false,'5.7.4'=>false,'5.7.5'=>true,'8.0.40'=>true,'8.4.4-commercial'=>true,'5.5.5-10.11.6-MariaDB'=>true,'10.0.1-MariaDB'=>false,'10.0.2-MariaDB'=>true,''=>false,'unknown'=>false) as $version=>$expected) {
    $wpdb->version=$version;
    if ((bool)Afrand_Wallet_Bridge::supports_wallet_locks()!==$expected) throw new Exception('Version guard failed: '.$version);
}
echo "PASS: 10 database version guard cases (stub; not WordPress integration)\n";
