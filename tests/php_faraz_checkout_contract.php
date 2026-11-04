<?php
// Routing isolation: native financial handlers are not replaced or bypassed.
define('ABSPATH', __DIR__.'/');
if (in_array('--rest', $argv, true)) define('REST_REQUEST', true);
$settings = array('enabled'=>true, 'page_id'=>99); $admin=false; $content='[woocommerce_checkout]'; $referer=false;
function wp_get_referer() { global $referer; return $referer; }
function get_option($key, $default=null) { global $settings; return $settings; }
function get_post($id) { global $content; return (object)array('ID'=>$id,'post_type'=>'page','post_status'=>'publish','post_content'=>$content); }
function has_shortcode($s,$tag) { return strpos($s,'['.$tag.']')!==false; }
function has_block($b,$p) { return strpos($p->post_content,'wp:woocommerce/checkout')!==false; }
function is_admin() { global $admin; return $admin; }
function get_permalink($id) { return 'https://shop.example/afrand-wallet-checkout/'; }
function wp_parse_url($v,$part) { return parse_url($v,$part); }
function trailingslashit($v) { return rtrim($v,'/').'/'; }
function add_action(...$args) {} function add_filter(...$args) {}
require __DIR__.'/../wordpress/afrand-faraz-wallet-bridge/checkout-compatibility.php';
function verify($v,$m) { if (!$v) throw new Exception($m); }
$classic = !defined('REST_REQUEST');
$_SERVER['REQUEST_URI']='/afrand-wallet-checkout/';
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===($classic?99:9),'Own form routing');
verify(Afrand_Faraz_Checkout_Compatibility::url('original')===($classic?get_permalink(99):'original'),'Own URL');
$_SERVER['REQUEST_URI']='/afrand-wallet-checkout/order-received/55/?key=private';
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===($classic?99:9),'Order endpoint');
foreach (array('/checkout/','/cart/','/wp-json/wc/store/v1/checkout','/afrand-wallet-checkout-other/') as $path) {
    $_SERVER['REQUEST_URI']=$path;
    verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Unrelated/block request changed: '.$path);
    verify(Afrand_Faraz_Checkout_Compatibility::url('original')==='original','Store URL changed without opt-in');
}
foreach (array('update_order_review','checkout') as $ajax) {
    $_GET['wc-ajax']=$ajax;
    foreach (array(false,'https://shop.example/checkout/','https://other.example/afrand-wallet-checkout/') as $referer) {
        verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Unrelated classic AJAX changed');
    }
    $referer='https://shop.example/afrand-wallet-checkout/';
    verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===($classic?99:9),'Own classic AJAX routing');
    verify(Afrand_Faraz_Checkout_Compatibility::url('original')===($classic?get_permalink(99):'original'),'Own AJAX return URL');
    $referer='https://shop.example/?wc-ajax=update_order_review';
    $_SERVER['HTTP_REFERER']='https://shop.example/afrand-wallet-checkout/';
    verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===($classic?99:9),'WooCommerce hidden AJAX referer must not hide own form');
    $referer='https://shop.example/afrand-wallet-checkout/';
    $_SERVER['HTTP_REFERER']='https://shop.example/checkout/';
    verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Header must keep unrelated form isolated');
    unset($_SERVER['HTTP_REFERER']);
}
$_GET['wc-ajax']='get_refreshed_fragments';
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Unrelated AJAX');
$_GET=array(); $_SERVER['REQUEST_URI']='/afrand-wallet-checkout/';
$admin=true;
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Admin changed');
$admin=false; $content='<!-- wp:woocommerce/checkout --> [woocommerce_checkout]';
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Block page bypassed native guard');
verify(Afrand_Faraz_Checkout_Compatibility::url('original')==='original','Invalid page URL');
$content='[woocommerce_checkout]'; $_SERVER['REQUEST_URI']='/cart/';
$settings['route_store']=true;
verify(Afrand_Faraz_Checkout_Compatibility::url('original')===get_permalink(99),'Explicit store opt-in');
unset($settings['route_store']);
verify(Afrand_Faraz_Checkout_Compatibility::url('original')==='original','Legacy option must not imply store opt-in');
$content='[woocommerce_checkout]'; $settings['enabled']=false;
verify(Afrand_Faraz_Checkout_Compatibility::url('original')==='original','Disabled route');
verify(Afrand_Faraz_Checkout_Compatibility::page_id(9)===9,'Disabled ID');
echo "PASS: separate classic page, endpoints, classic AJAX, block/REST/admin isolation, invalid page and disable\n";
