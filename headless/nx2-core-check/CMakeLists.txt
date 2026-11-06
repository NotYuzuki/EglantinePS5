cmake_minimum_required(VERSION 3.24)
project(eglantine_nx2_core_check LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
include(FetchContent)
file(READ "${EDEN_SOURCE}/cpmfile.json" packages)
string(JSON catch_version GET "${packages}" catch2 version)
string(JSON catch_hash GET "${packages}" catch2 hash)
set(CATCH_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(CATCH_INSTALL_DOCS OFF CACHE BOOL "" FORCE)
set(CATCH_INSTALL_EXTRAS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(Catch2
    URL "https://github.com/catchorg/Catch2/archive/refs/tags/${catch_version}.tar.gz"
    URL_HASH "SHA512=${catch_hash}")
FetchContent_MakeAvailable(Catch2)
file(GLOB ampere_tests CONFIGURE_DEPENDS "${EDEN_SOURCE}/src/tests/video_core/ampere_*.cpp")
# These integration tests link the complete shader compiler and run in upstream tests.
# This host check exercises the header-only native capture/lowering helpers.
list(FILTER ampere_tests EXCLUDE REGEX
     "ampere_(async_copy|bindless_tex|fast_fusion_logged|geometry|logged_numeric|logged_textures|starfox_test10|sured|tennis_tessellation)\\.cpp$")
# The readback, mapping-table, sparse-region and memory-budget tests left with the Lichii merge
# (the GPU memory registry replaced them).
add_executable(nx2-core-check ${ampere_tests})
file(GLOB boost_includes "${DEPENDENCY_SOURCE}/.cache/cpm/boost/*/libs/*/include")
target_include_directories(nx2-core-check PRIVATE "${EDEN_SOURCE}/src" "${FMT_INCLUDE}" ${boost_includes})
target_link_libraries(nx2-core-check PRIVATE Catch2::Catch2WithMain)
